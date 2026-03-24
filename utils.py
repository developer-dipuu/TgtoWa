"""
Utility functions for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import os
import re
import zipfile
import glob
import asyncio
import tempfile
import shutil
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Any
from telethon import TelegramClient
from telethon.tl.types import InputStickerSetShortName, InputStickerSetID, Document
from telethon.errors.rpcerrorlist import StickersetInvalidError
from telethon.tl.functions.messages import GetStickerSetRequest
from PIL import Image
import io
import emoji
import regex

from config import (DOWNLOAD_TIMEOUT, UPLOAD_TIMEOUT, DB_UPLOAD_TIMEOUT, DB_DUMP_TIMEOUT, MAX_DOWNLOAD_RETRIES, MAX_UPLOAD_RETRIES, OWNER_ID, 
                    BACKUP_ENABLED, BACKUPS, BACKUP_GROUP_ID, LOG_DIR, TEMP_DIR, DB_USER, DB_HOST, DB_PORT, DB_NAME, DB_PASSWORD)

logger = logging.getLogger(__name__)


class FileUploadTimeoutError(asyncio.TimeoutError):
    """A custom timeout error that includes which file failed."""
    def __init__(self, message, *, index, file_path):
        super().__init__(message)
        self.index = index
        self.file_path = file_path

class FileUploadWrapperError(Exception):
    """Wraps a general exception with context about which file failed."""
    def __init__(self, message, *, index, file_path, original_exception):
        super().__init__(message)
        self.index = index
        self.file_path = file_path
        self.original_exception = original_exception

def ensure_directories():
    """Create necessary directories if they don't exist"""
    from config import TEMP_DIR, OUTPUT_DIR
    
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_pack_name_from_url(url: str) -> Optional[str]:
    """Extract sticker/emoji pack name from Telegram URL"""
    patterns = [
        r't\.me/addstickers/(.+)',
        r'telegram\.me/addstickers/(.+)',
        r'addstickers/(.+)',
        r't\.me/addemoji/(.+)',
        r'telegram\.me/addemoji/(.+)',
        r'addemoji/(.+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

class NetworkTask:
    def __init__(self, client: TelegramClient):
        self.client = client

    # to get the sticker object (information, its not downloading the pack), it is called in the handle_message
    async def get_sticker_set(self, pack_input: Any, access_hash: int | None = None):
        """
        Get sticker/emoji set from Telegram using either a short name (str) 
        or a concrete InputStickerSet type (like InputStickerSetID).
        """
        try:
            input_set = None
            if isinstance(pack_input, str):
                # If we get a string, we assume it's a short_name
                input_set = InputStickerSetShortName(short_name=pack_input)
            elif isinstance(pack_input, int) and access_hash:
                input_set = InputStickerSetID(id=pack_input, access_hash=access_hash)
            elif isinstance(pack_input, (InputStickerSetID, InputStickerSetShortName)):
                # If we get a valid InputStickerSet object, use it directly
                input_set = pack_input
            else:
                logger.error(f"Invalid type provided for pack: {type(pack_input)}")
                return None
            sticker_set = await self.client(GetStickerSetRequest(
                stickerset=input_set,
                hash=0
            ))
            return sticker_set
        except StickersetInvalidError:
            logger.error(f"The set '{pack_input}' is invalid or does not exist.")
            return None
        except Exception as e:
            logger.error(f"Failed to get set {pack_input}: {e}")
            return None


    async def download_sticker(self, sticker: Document, temp_dir: str) -> Optional[str]:
        """
        Download a single sticker or emoji file with a retry mechanism.
        """
        file_path = os.path.join(temp_dir, f"sticker_{sticker.id}")
        max_retries = MAX_DOWNLOAD_RETRIES
        
        for attempt in range(max_retries):
            try:
                downloaded_path = await asyncio.wait_for(
                    self.client.download_media(sticker, file=file_path),
                    timeout=DOWNLOAD_TIMEOUT
                )
                if downloaded_path:
                    logger.info(f"Successfully downloaded item {sticker.id} to {downloaded_path} on attempt {attempt + 1}")
                    return downloaded_path
            except asyncio.TimeoutError:
                logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries} while downloading sticker {sticker.id}.")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))  # Wait 2, 4 seconds before retrying
                else:
                    logger.error(f"Failed to download sticker {sticker.id} after {max_retries} attempts due to timeout.")
                    return None
            except Exception as e:
                logger.error(f"Failed to download item {sticker.id} on attempt {attempt + 1} with error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1) # 1 sec wait for other error 
                else:
                    logger.error(f"All {max_retries} retries failed for sticker {sticker.id}.")
                    return None
        return None

    async def _upload_worker(self, file_path: str, index: int, timeout: int):
        """
        Worker task that uploads a file's bytes with a dedicated timeout and returns a handle.
        """
        try:
            for attempt in range(MAX_UPLOAD_RETRIES):
                try:
                    # upload temporarily with timeout
                    return await asyncio.wait_for(
                        self.client.upload_file(file_path),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Upload of {file_path} timed out on attempt {attempt + 1}/{MAX_UPLOAD_RETRIES}.")
                    if attempt == MAX_UPLOAD_RETRIES -1:
                        raise
                    wait_time = 2 * (attempt + 1)
                    await asyncio.sleep(wait_time)
                    pass
        except asyncio.TimeoutError:
            # raise our detailed one
            raise FileUploadTimeoutError(
                f"Timeout during the initial upload phase of {file_path}",
                index=index,
                file_path=file_path
            )
        except Exception as e:
            # our detailed one again :)
            raise FileUploadWrapperError(
                f"Error during the initial upload phase of {file_path}: {e}",
                index=index,
                file_path=file_path,
                original_exception=e
            ) from e
        
    async def upload_files(self, file_paths: List[str], pack_url: str, pack_title: str, chat_id: int):
        """
        Uploads files using a two-phase approach with robust, dynamic timeouts and a TaskGroup.
        """
        num_files = len(file_paths)
        timeout = (num_files * UPLOAD_TIMEOUT)

        file_handles = [None] * num_files # this will hold results in order
        tasks = []

        try:
            # ------ Phase 1: Parallel Upload  --------------
            async with asyncio.TaskGroup() as tg:
                for i, file_path in enumerate(file_paths):
                    # Create a task for each file upload
                    task = tg.create_task(self._upload_worker(file_path, i, timeout=timeout))
                    tasks.append(task)
        except* (FileUploadTimeoutError, FileUploadWrapperError) as eg:
            # We re raise the group to be handled by bot_handlers.py
            raise

        # If done successfully, get results from the ordered task list
        for i, task in enumerate(tasks):
            file_handles[i] = task.result()


        # ----- Phase 2: Sequential Sending (to maintain order yk)--------------------
        messages = []
        for i, handle in enumerate(file_handles):
            if handle is None: return [] # extra safety, but there shouldnt be any, but nvm lets do it
            file_path = file_paths[i]
            caption = f"<tg-emoji emoji-id='5785045099142450328'>📦</tg-emoji> <a href=\"{pack_url}\">{pack_title}</a> - Part {i+1}/{num_files}\nSize: {format_file_size(os.path.getsize(file_path))}"
            try:
                message = await asyncio.wait_for(
                    self.client.send_file(chat_id, handle, caption=caption, link_preview=False, parse_mode='html'),
                    timeout=10
                )
                messages.append(message)
            except asyncio.TimeoutError:
                raise FileUploadTimeoutError(
                    f"Timeout during the final send phase of file: {file_path}",
                    index=i,
                    file_path=file_path
                )
            except Exception as e:
                raise FileUploadWrapperError(
                    f"Error during the final send phase of file {file_path}: {e}",
                    index=i,
                    file_path=file_path,
                    original_exception=e
                ) from e
        return messages


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

            # Create a dictionary for the PGPASSWORD environment variable
            # This is more secure than putting the password in the command itself
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
                '--clean', # Add this to drop existing objects before recreating
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
            if dump_path and os.path.exists(dump_path):
                os.remove(dump_path) # Clean up the .sql dump
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path) # Clean up the .zip file

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
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)

    async def run_backup(self):
        """Main entry point to run all enabled backups."""
        if not BACKUP_ENABLED:
            return

        if BACKUPS.get("database", {}).get("enabled"):
            await self._backup_database()
        
        await asyncio.sleep(5) 
        
        if BACKUPS.get("logs", {}).get("enabled"):
            await self._backup_logs()


# we dont use it as images are already samll in size maybe we need it in future lets see
def optimize_image_size(image_data: bytes, max_size: int, dimensions: Tuple[int, int]) -> bytes:
    """Optimize image to meet size constraints while maintaining quality"""
    img = Image.open(io.BytesIO(image_data))
    
    # Convert to RGBA if not already
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    # resize to target dimensions
    img = img.resize(dimensions, Image.Resampling.LANCZOS)
    
    # Try different quality settings to meet size cap
    for quality in range(95, 10, -5):
        output = io.BytesIO()
        img.save(output, format='WEBP', quality=quality, optimize=True)
        
        if output.tell() <= max_size:
            return output.getvalue()
    
    # If still too large try with minimal quality
    output = io.BytesIO()
    img.save(output, format='WEBP', quality=10, optimize=True)
    return output.getvalue()

def create_temp_directory() -> str:
    """Create a temporary directory for processing"""
    from config import TEMP_DIR
    return tempfile.mkdtemp(dir=TEMP_DIR)

def cleanup_temp_directory(temp_dir: str):
    """Clean up temporary directory"""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)

# to skip telegram renaming files if finds non ascii or non emoji characters
def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage"""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')

    # replace non ascii and non emoji characters by _ but only once for consecutive ones
    new_filename = ""
    for character in regex.findall(r'\X', filename):
        if character.isascii() or emoji.is_emoji(character):
            new_filename += character
        else:
            if not new_filename.endswith("_"):
                new_filename += "_"

    # combination of space and underscore to single underscore
    new_filename = re.sub(r'[_\s]*_[_\s]*', '_', new_filename).strip("_")
    
    # two or more space to single space
    new_filename = re.sub(r'\s{2,}', ' ', new_filename)

    # Limit length
    if len(new_filename) > 30:
        new_filename = new_filename[:30]

    return new_filename or "Converted_pack"


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def get_user_display_name(user) -> str:
    """Get user display name with fallback"""
    
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else :
        return str(user.id)


def is_valid_sticker_url(url: str) -> bool:
    """Check if URL is a valid Telegram sticker or emoji pack URL"""
    return extract_pack_name_from_url(url) is not None

def estimate_wait_time(sticker_doc_info: list, num_packs: int | None = None) -> float:
    """
    Calculates estimated wait time in seconds based on the type of each sticker/emoji.
    """
    total_seconds = 0.0

    # Time for processing each sticker/emoji
    for doc in sticker_doc_info:
        if doc == 'application/x-tgsticker':  # TGS file
            total_seconds += 2
        elif doc == 'video/webm':  # WebM
            total_seconds += 1
        elif doc == 'image/webp': # WebP
            total_seconds += 0.1
        else: # Others if any, we prbbly wont get any
            total_seconds += 1

    """Uncomment this part if you want to add some more logic to estimated time based on number of packs it will create
    Like if your machine has delays in downloading packs so you may add 10 sec for each pack to the estimated time"""
    # if num_packs:
    #     total_seconds += 10*num_packs
    
    return total_seconds