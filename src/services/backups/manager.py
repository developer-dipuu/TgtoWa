import os
import glob
import zipfile
import asyncio
import logging
from telethon import TelegramClient
from datetime import datetime, timezone
from src.core.config import *

logger = logging.getLogger(__name__)

class BackupManager:
    """Handles the creation and upload of daily backups."""
    def __init__(self, client: TelegramClient):
        self.client = client

    async def _create_zip_archive(self, files_to_add: list, zip_path: str):
        """Creates a zip archive from a list of files in a non-blocking way."""
        def zip_files():
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in files_to_add:
                    if os.path.exists(file_path):
                        # arcname ensures we dont store the full directory structure
                        arcname = os.path.basename(file_path)
                        zf.write(file_path, arcname)
        
        await asyncio.to_thread(zip_files)

    async def _backup_database(self):
        """Handles creating a database dump, zipping it, and uploading."""
        logger.info("Starting database backup...")
        dump_path = None
        zip_path = None
        try:
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            dump_filename = f"db_backup_{date_str}.sql"
            dump_path = os.path.join(TEMP_DIR, dump_filename)

            env = os.environ.copy()
            env['PGPASSWORD'] = DB_PASSWORD

            # The command to dump the database to a file
            process = await asyncio.create_subprocess_exec(
                'pg_dump',
                '-U', DB_USER,
                '-h', DB_HOST,
                '-p', str(DB_PORT),
                '-d', DB_NAME,
                '-f', dump_path,
                '--no-owner',
                '--clean',
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=DB_DUMP_TIMEOUT)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error(f"Database backup pg_dump timed out after {DB_DUMP_TIMEOUT} seconds.")
                await self.client.send_message(OWNER_ID, f"❌ **Database Backup Failed!**\n\n`pg_dump` timed out after {DB_DUMP_TIMEOUT} seconds.")
                return
            if process.returncode != 0:
                error_message = stderr.decode(errors="replace").strip() if stderr else "No error output"
                logger.error(f"pg_dump failed with return code {process.returncode}: {error_message}")
                await self.client.send_message(OWNER_ID, f"❌ **Database Backup Failed!**\n\n`pg_dump` error:\n```{error_message}```")
                return

            logger.info(f"Database dump created successfully at {dump_path}")

            zip_filename = f"db_backup_{date_str}.zip"
            zip_path = os.path.join(TEMP_DIR, zip_filename)

            await self._create_zip_archive([dump_path], zip_path)

            caption = f"#db_backup\nDate: {date_str} UTC"
            try:
                await asyncio.wait_for(self.client.send_file(
                    BACKUP_GROUP_ID,
                    file=zip_path,
                    caption=caption
                ), timeout=DB_UPLOAD_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"Database backup upload timed out after {DB_UPLOAD_TIMEOUT} seconds.")
                await self.client.send_message(OWNER_ID, f"❌ **Database Backup Failed!**\n\nUpload timed out after {DB_UPLOAD_TIMEOUT} seconds.")
                return
            logger.info("Successfully uploaded database backup.")

        except Exception as e:
            logger.error(f"Database backup process failed: {e}", exc_info=True)
            await self.client.send_message(OWNER_ID, f"❌ **Database Backup Failed!**\n\nError:\n```{e}```")
        finally:
            try:
                if dump_path and os.path.exists(dump_path):
                    os.remove(dump_path) # Clean up the .sql dump
                if zip_path and os.path.exists(zip_path):
                    os.remove(zip_path) # Clean up the .zip file
            except Exception as e:
                logger.error(f"Error removing database backup files: {e}")

    async def _backup_logs(self):
        """Handles zipping and uploading log files."""
        logger.info("Starting log files backup...")
        zip_path = None
        try:
            log_dir_path = os.path.realpath(os.path.expanduser(LOG_DIR))
            if not os.path.isdir(log_dir_path):
                logger.warning(f"Log backup skipped: Log directory '{log_dir_path}' not found.")
                return

            log_files = glob.glob(os.path.join(log_dir_path, '*'))
            if not log_files:
                logger.info("Log backup skipped: No log files found.")
                return

            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            zip_filename = f"logs_backup_{date_str}.zip"
            zip_path = os.path.join(TEMP_DIR, zip_filename)

            await self._create_zip_archive(log_files, zip_path)

            caption = f"#log_backup\nDate: {date_str} UTC"
            await self.client.send_file(
                BACKUP_GROUP_ID,
                file=zip_path,
                caption=caption
            )
            logger.info("Successfully uploaded log files backup.")

        except Exception as e:
            logger.error(f"Log files backup process failed: {e}", exc_info=True)
        finally:
            try:
                if zip_path and os.path.exists(zip_path):
                    os.remove(zip_path)
            except Exception as e:
                logger.error(f"Error removing logs zip file: {e}")

    async def run_backup(self):
        """Main entry point to run all enabled backups."""
        if not BACKUP_ENABLED:
            return

        if BACKUPS.get("database", {}).get("enabled"):
            await self._backup_database()
        
        await asyncio.sleep(5) 
        
        if BACKUPS.get("logs", {}).get("enabled"):
            await self._backup_logs()

