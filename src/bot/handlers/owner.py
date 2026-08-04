import os
import re
import glob
import json
import asyncio
import logging
import zipfile
from datetime import datetime, timezone
from telethon import events, Button
from telethon.events import StopPropagation

from src import db
from src.core.config import *
from src.bot.handlers.context import BotContext
from src.utils.parsers import extract_pack_name_from_url
from src.services.queue.manager import queue_manager
from src.services.sessions.manager import session_manager, Flow, Session


logger = logging.getLogger(__name__)

class OwnerCommands:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx
    # action helper
    async def _propose_action(self, event, action_type: str, target_ids: list, message_to_send, text_to_send, no_forward, silent_broadcast):
        """Handles the confirmation flow for /send and /broadcast."""
        action_id = os.urandom(8).hex()

        # Store pending action details
        self.ctx.pending_actions[action_id] = {
            "action_type": action_type,
            "target_ids": target_ids,
            "message_to_send": message_to_send,
            "text_to_send": text_to_send,
            "no_forward": no_forward,
            "silent": silent_broadcast
        }

        # Send preview to owner
        preview_header = (
            f"**PREVIEW for `{action_type.upper()}`**\n\n"
            f"This message will be sent to **{len(target_ids)}** user(s)."
        )
        await self.ctx.client.send_message(event.chat_id, preview_header)

        # Send the actual content preview
        if text_to_send: 
            # Scenario: pure text from the command
            await self.ctx.client.send_message(event.chat_id, text_to_send, silent= silent_broadcast, link_preview=False)
        elif message_to_send: 
            if no_forward:
                # Scenario: Send a copy of the replied/media message
                await self.ctx.client.send_message(event.chat_id, message_to_send, silent= silent_broadcast)
            else:
                # Scenario: Forward the replied/media message
                await self.ctx.client.forward_messages(event.chat_id, message_to_send, silent= silent_broadcast)

        # Send confirmation prompt
        buttons = [
            [Button.inline(f"✅ Yes, {action_type.capitalize()}", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await self.ctx.client.send_message(event.chat_id, f"Do you want to proceed with this {action_type}?", buttons=buttons)
        raise StopPropagation


    async def broadcast_command(self, event: events.NewMessage.Event):
        """Owner command to broadcast a message to all users."""
        
        message_to_broadcast = None
        text_to_broadcast = None
        
        # Parse arguments and flags from the command text
        command_parts = event.text.split()
        flags = {part.lower() for part in command_parts[:3] if part.startswith('-')}

        # Check for flags first
        no_forward = '-nf' in flags
        silent_broadcast = '-s' in flags
        
        # Scenario 1: Replying to a message to broadcast its content
        replied_msg = await event.get_reply_message()
        if replied_msg:
            message_to_broadcast = replied_msg
        else:
            # Scenario 2: Command with its own media/forward or just text
            if event.media or event.message.forward:
                # Use the command message itself if it contains media or is a forward
                message_to_broadcast = event.message
            else:
                # Scenario 3: Extract text content from the command message stripping the command and flags        
                content_index = 1
                for part in command_parts[1:3]:
                    if part.lower() in ('-nf', '-s'):
                        content_index += 1

                if len(command_parts) > content_index:
                    text_to_broadcast = " ".join(command_parts[content_index:])

        # If no content is found, show detailed usage instructions
        if not message_to_broadcast and not text_to_broadcast:
            await event.reply(
                "ℹ️ **Usage:** Reply to a message with `/broadcast [-nf] [-s]`\n"
                "or send `/broadcast [-nf] [-s] <your message>`.\n\n"
                "• `-nf`: Send as a copy instead of forwarding (no forward tag).\n"
                "• `-s`: Send silently (no notification for users)."
            )
            return


        # all users we have 
        user_ids = await db.get_all_user_ids() 
        if not user_ids:
            await event.reply("❌ No users found in the database to broadcast to.")
            return

        # call the helper to prompt for confirmation, he'll handle the rest
        await self._propose_action(
            event, 'broadcast', user_ids, message_to_broadcast, 
            text_to_broadcast, no_forward, silent_broadcast
        )


    async def send_command(self, event: events.NewMessage.Event):
        """Owner command to send a message to specific users with confirmation."""
        message_to_send = None
        text_to_send = None

        command_parts = event.text.split()
        flags = {part.lower() for part in command_parts[:3] if part.startswith('-')}
        no_forward = '-nf' in flags
        silent_broadcast = '-s' in flags

        # Extract user list from parentheses
        user_list_match = re.search(r'\((.*?)\)', event.text)
        if not user_list_match:
            await event.reply(
                "ℹ️ **Usage:** Reply to a message with `/send [-nf] [-s] (user1 @user2)`\n"
                "or send `/send [-nf] [-s] (user1 @user2) <your message>`.\n\n"
                "• `-nf`: Send as a copy instead of forwarding (no forward tag).\n"
                "• `-s`: Send silently (no notification for users).\n"
                "Note: User IDs/usernames must be in parentheses `()`."
            )
            return

        user_inputs = user_list_match.group(1).split()
        if not user_inputs:
            await event.reply("❌ The user list is either empty or not provided.")
            return

        # Resolve user inputs to IDs
        status_msg = await event.reply(f"Resolving {len(user_inputs)} user(s)...")
        target_ids = []
        failed_users = []
        for user_input in user_inputs:
            try:
                entity_to_find = user_input.strip()
                if entity_to_find.isdigit():
                    entity_to_find = int(entity_to_find)
                user_entity = await self.ctx.client.get_entity(entity_to_find)
                target_ids.append(user_entity.id)
            except Exception:
                failed_users.append(user_input)

        await status_msg.delete()
        if failed_users:
            await event.reply(f"❌ Could not find the following users: `{'`, `'.join(failed_users)}`")

        if not target_ids:
            await event.reply("❌ No valid users found to send the message to.")
            return

        # Remove the user list and flags from the text to get the message content
        text_without_users = re.sub(r'\((.*?)\)', '', event.text).strip()

        text_parts = text_without_users.split()
        text_without_flags = None
        content_index = 1
        for part in text_parts[1:3]:
            if part.lower() in ('-nf', '-s'):
                content_index += 1
        if len(text_parts) > content_index:
            text_without_flags = " ".join(text_parts[content_index:])


        replied_msg = await event.get_reply_message()
        if replied_msg:
            message_to_send = replied_msg
        elif text_without_flags:
            text_to_send = text_without_flags

        if not message_to_send and not text_to_send:
            await event.reply("❌ No message content found. Please reply to a message or type your message after the user list.")
            return

        await self._propose_action(
            event, 'send', list(set(target_ids)), message_to_send, 
            text_to_send, no_forward, silent_broadcast
        )

    async def gstats_command(self, event: events.NewMessage.Event):
        """Owner command to view global bot statistics."""
        message, buttons = await self.ctx.templates.get_gstats_message_and_buttons()
        await event.reply(message, buttons=buttons)
        raise StopPropagation

    # owner's command
    async def promote_command(self, event: events.NewMessage.Event):
        """Owner command to promote a user to admin."""
        if not db.is_owner(event.sender_id):
            return # Silently ignore for non-owners
        
        try:
            target_user = await self.ctx.helpers.get_user_from_event(event, event.pattern_match.group(1))
            if not target_user:
                await event.reply("ℹ️ Usage: `/promote <user_id/@username>` or reply to a user's message.")
                return

            if await db.is_admin(target_user.id):
                await event.reply(f"️🤷‍♂️ User `{target_user.id}` is already an admin.")
                return
                
            if await db.add_admin(target_user.id, target_user.username, event.sender_id):
                full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
                await event.reply(f"👑 Successfully promoted **{full_name}** (`{target_user.id}`) to Admin!")
                logger.info(f"User {target_user.id} promoted to admin by {event.sender_id}")
            else:
                await event.reply("❌ Failed to promote user. Maybe they are already an admin.")
        except Exception as e:
            await event.reply(f"An error has occurred:\n```{e}```")
            logger.info(f"An error has occurred while promoting someone to admin by {event.sender_id}. Error: {e}")
        raise StopPropagation

    # owner's command
    async def demote_command(self, event: events.NewMessage.Event):
        """Owner command to demote an admin."""
        if not db.is_owner(event.sender_id):
            return

        try:
            target_user = await self.ctx.helpers.get_user_from_event(event, event.pattern_match.group(1))
            if not target_user:
                await event.reply("ℹ️ Usage: `/demote <user_id/@username>` or reply to a user's message.")
                return

            if not await db.is_admin(target_user.id) or db.is_owner(target_user.id):
                await event.reply(f"🤷‍♂️ User `{target_user.id}` is not a promotable/demotable admin.")
                return

            if await db.remove_admin(target_user.id, event.sender_id):
                full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
                await event.reply(f"✅ Successfully demoted **{full_name}** (`{target_user.id}`).")
                logger.info(f"User {target_user.id} demoted by {event.sender_id}")
            else:
                await event.reply("❌ Failed to demote user. Are you sure they are an admin?")
        except Exception as e:
            await event.reply(f"An error has occurred:\n```{e}```")
            logger.info(f"An error has occurred while demoting someone by {event.sender_id}. Error: {e}")
        raise StopPropagation


    async def getdb_command(self, event: events.NewMessage.Event):
        """Owner command to get a dump of the PostgreSQL database."""
        dump_path = None
        try:
            logger.info(f"Owner {event.sender_id} requested the database file.")
            
            # Create a temporary file path for the dump
            dump_filename = f"bot_db_dump_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
            dump_path = os.path.join(TEMP_DIR, dump_filename)

            # Let the user owner what's happening
            status_msg = await event.reply("⚙️ Creating database dump...")

            env = os.environ.copy()
            env['PGPASSWORD'] = DB_PASSWORD

            process = await asyncio.create_subprocess_exec(
                'pg_dump',
                '-U', DB_USER,
                '-h', DB_HOST,
                '-p', str(DB_PORT),
                '-d', DB_NAME,
                '-f', dump_path,
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
                logger.error("Database dump timed out.")
                await event.reply("❌ Error: The database dump process timed out.")
                return
            if process.returncode != 0:
                error_message = stderr.decode(errors="replace").strip() if stderr else "No error output"
                logger.error(f"getdb failed: pg_dump returned {process.returncode}: {error_message}")
                await status_msg.edit(f"❌ Error creating dump:\n```{error_message}```")
                return
            logger.info(f"Database dump created successfully at {dump_path}")

            await status_msg.edit("⬆️ Uploading database dump...")
            try:
                await asyncio.wait_for(status_msg.edit("📦 Here is the database dump file.", file = dump_path), timeout=DB_UPLOAD_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error("Database upload timed out.")
                await event.reply("❌ Error: The database upload timed out.")

            logger.info("Successfully uploaded database dump file.")

        except Exception as e:
            logger.error(f"An error occurred during /getdb: {e}", exc_info=True)
            await event.reply(f"❌ An unexpected error occurred:\n```{e}```")
        finally:
            try:
                # Clean up the temporary dump file
                if dump_path and os.path.exists(dump_path):
                    os.remove(dump_path)
            except Exception as e:
                logger.error(f"Error removing database dump file: {e}")
        
        raise StopPropagation

    async def getlogs_command(self, event: events.NewMessage.Event):
        """Owner command to get the screen log files."""        
        log_dir = os.path.realpath(os.path.expanduser(LOG_DIR))
        args = event.text.split()

        # Determine if 'all' argument is present
        get_all = len(args) > 1 and args[1].lower() == 'all'
        
        if not os.path.exists(log_dir):
            logger.error(f"Owner {event.sender_id} requested logs, but log directory '{log_dir}' not found.")
            await event.reply("❌ Error: Log directory not found.")
            return

        if get_all: # Send all logs as a zip 
            try:
                logger.info(f"Owner {event.sender_id} requested all log files.")
                all_logs = glob.glob(os.path.join(log_dir, '*'))
                if not all_logs:
                    await event.reply("🤔 The log directory is empty.")
                    return

                zip_path = os.path.join(TEMP_DIR, "bot_logs.zip")
                
                await event.reply(f"📦 Zipping up {len(all_logs)} log files. Please wait...")

                # Create a zip file in a separate thread to avoid blocking
                def create_zip():
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for log_file in all_logs:
                            zf.write(log_file, os.path.basename(log_file))

                try:# wait for zipping to complete upto 60 sec
                    await asyncio.wait_for(asyncio.to_thread(create_zip), 60)
                except asyncio.TimeoutError:
                    logger.error(f"Stopped creating zip because it was taking too much time.")
                    await event.reply(f"Creating zip failed, it is taking too much time. Maybe log files are too big or something unexpected is there in the logs directory.")
                    return
                
                try:# wait for upload to complete for upto UPLOAD_TIMEOUT seconds
                    await asyncio.wait_for(self.ctx.client.send_file(event.chat_id, zip_path, caption=f"Here are all {len(all_logs)} log files."), UPLOAD_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error(f"Logs file upload timed out.")
                    await event.reply("Error: Logs file upload timed out.")
                    return
                except Exception as e:
                    logger.error(f"Logs file upload failed. Error: {e}")
                    await event.reply(f"Error: Logs file upload failed.\n**Error**: {e}")
                    return
            except Exception as e:
                logger.error(f"An error occured: {e}")
                await event.reply(f"Error: An error occured: {e}")
            finally:
                try:
                    if os.path.exists(zip_path):
                        os.remove(zip_path) # Clean up the zip file
                except Exception as e:
                    logger.error(f"Error removing logs zip file: {e}")

        else: # Send the latest (current) log
            logger.info(f"Owner {event.sender_id} requested the latest log file.")
            # Find the uncompressed .log file (logrotate leaves today's log uncompressed)
            # .screenrc names it based on session and window e.g. tgBot-0.log
            try:
                latest_logs = glob.glob(os.path.join(log_dir, '*.log'))
                latest_logs = [f for f in latest_logs if os.path.getsize(f) > 0]
                latest_logs.sort(key=os.path.getmtime, reverse=True)
                if latest_logs:
                    # Assuming the first one found is the active one
                    latest_log_path = latest_logs[0]
                    try:
                        await asyncio.wait_for(event.reply("📄 Here is the current log file.", file=latest_log_path), UPLOAD_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.error(f"Logs file upload timed out.")
                        await event.reply("Error: Logs file upload timed out.")
                        return
                    except Exception as e:
                        logger.error(f"Logs file upload failed. Error: {e}")
                        await event.reply(f"Error: Logs file upload failed.\n**Error**: {e}")
                        return
                else:
                    logger.error(f"Warning: No .log files found in {log_dir}")
                    await event.reply("🤔 No `.log` file found. Seems something's wrong.")
            except Exception as e:
                logger.error(f"An error occured while getting logs: {e}")
                await event.reply(f"Error: An error occured while getting logs.\n**Error**: {e}")
        raise StopPropagation
    
    async def toggle_cache_command(self, event: events.NewMessage.Event):
        """Owner command to enable or disable the caching system."""

        arg = event.text.split(maxsplit=1)

        if len(arg) > 1 and arg[1].lower() == 'on':
            self.ctx.cache_enabled = True
            await event.reply("✅ Caching system has been **enabled**.")
        elif len(arg) > 1 and arg[1].lower() == 'off':
            self.ctx.cache_enabled = False
            await event.reply("❌ Caching system has been **disabled**.")
        else:
            status = "ENABLED" if self.ctx.cache_enabled else "DISABLED"
            await event.reply(
                f"ℹ️ The caching system is currently **{status}**.\n\n"
                "Usage: `/togglecache <on|off>`"
            )
        raise StopPropagation

    async def clearcache_command(self, event: events.NewMessage.Event):
        """Owner command to clear the cache for all or specific packs."""
        args = event.text.split()[1:]

        if not args:
            await event.reply(
                "ℹ️ **Usage:**\n"
                "• `/clearcache all` - Clear the entire cache.\n"
                "• `/clearcache <link1> <link2> ...` - Clear specific packs from the cache."
            )
            return

        action_id = os.urandom(8).hex()
        action_type = ""
        confirm_message = ""
        action_payload = {}

        if args[0].lower() == 'all':
            all_packs = await db.get_all_cached_pack_ids()
            if not all_packs:
                await event.reply("✅ The cache is already empty. Nothing to do!")
                return
            
            action_type = "clearcache_all"
            confirm_message = (
                f"🗑️ Are you sure you want to clear the **entire cache**? "
                f"This will remove **{len(all_packs)}** packs, cannot be undone, "
                f"and may take some time to complete."
            )
            action_payload = {"packs_to_clear": set(all_packs)}

        else: # It's a list of links
            pack_names = [extract_pack_name_from_url(link) for link in args]
            valid_packs = [name for name in pack_names if name]

            if not valid_packs:
                await event.reply("❌ No valid sticker/emoji pack links found in your message.")
                return

            action_type = "clearcache_packs"
            confirm_message = f"🗑️ You are about to clear the cache for **{len(valid_packs)}** pack(s). Are you sure you want to proceed?"
            action_payload = {"pack_short_names": valid_packs}
            
        self.ctx.pending_actions[action_id] = {
            "action_type": action_type,
            "payload": action_payload,
        }

        buttons = [
            [Button.inline("✅ Yes, Proceed", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await event.reply(confirm_message, buttons=buttons)
        raise StopPropagation


    async def refreshcache_command(self, event: events.NewMessage.Event):
        """Owner command to refresh the cache for top or specific packs."""
        if self.ctx.active_refresh_jobs:
            await event.reply(
                "⚠️ A cache refresh operation is already in progress.\n"
                "Please wait for it to complete, or use /cancelrefresh to stop it.",
                buttons=[[Button.inline("❌ Cancel Current Refresh", b"cancel_refresh_prompt")]]
            )
            return

        args = event.text.split()[1:]
        action_id = os.urandom(8).hex()
        action_type = ""
        confirm_message = ""
        action_payload = {}

        if not args or (len(args) == 1 and args[0].isdigit()):
            limit = int(args[0]) if args else "all"
            action_type = "refreshcache_top_n"
            confirm_message = f"🔄 This will **clear the entire cache** and then re-cache {"**ALL** packs." if limit == "all" else f"the top **{limit}** packs based on score"}. This may take a while.\n\nAre you sure?"
            action_payload = {"limit": limit}
        else:
            pack_names = [extract_pack_name_from_url(link) for link in args]
            valid_packs = [name for name in pack_names if name]
            if not valid_packs:
                await event.reply("❌ No valid sticker/emoji pack links found.")
                return
            
            action_type = "refreshcache_links"
            confirm_message = f"🔄 This will clear and re-cache **{len(valid_packs)}** specific pack(s).\n\nAre you sure?"
            action_payload = {"pack_short_names": valid_packs}
        original_event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id, 'bot_reply_message_id': 0}
        self.ctx.pending_actions[action_id] = { "action_type": action_type, "payload": action_payload, "original_event_info": original_event_info }
        buttons = [
            [Button.inline("✅ Yes, Refresh", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await event.reply(confirm_message, buttons=buttons)
        raise StopPropagation

    async def cancelrefresh_command(self, event: events.NewMessage.Event):
        """Owner command to cancel an ongoing cache refresh operation."""
        if not self.ctx.active_refresh_jobs:
            await event.reply("✅ No active cache refresh operation to cancel.")
            return

        msg = await event.reply(f"Cancelling {len(self.ctx.active_refresh_jobs)} queued refresh jobs...")
        
        cancelled_count = 0
        # Create a copy to iterate over, as the set will be modified
        jobs_to_cancel = list(self.ctx.active_refresh_jobs)
        for log_id in jobs_to_cancel:
            # The OWNER_ID is used as the user_id for system tasks
            if await queue_manager.cancel_item(user_id=SYSTEM_USER_ID, log_id=log_id):
                await db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
                cancelled_count += 1

        self.ctx.active_refresh_jobs.clear()
        
        if self.ctx.active_refresh_message:
            try:
                await self.ctx.client.edit_message(self.ctx.active_refresh_message.chat_id, self.ctx.active_refresh_message.id, "❌ Cache refresh operation cancelled by user.")
                self.ctx.active_refresh_message = None
            except Exception:
                pass # Message might have been deleted

        await msg.edit(f"✅ Cancelled **{cancelled_count}** pending jobs from the queue.")
        raise StopPropagation

    async def addcache_command(self, event: events.NewMessage.Event):
        """Owner command to add non-cached packs to the cache."""
        if self.ctx.active_add_jobs:
            await event.reply(
                "⚠️ An add-to-cache operation is already in progress.\n"
                "Please wait for it to complete, or use /canceladdcache to stop it.",
                buttons=[[Button.inline("❌ Cancel Current Add-Cache", b"cancel_addcache_prompt")]]
            )
            return

        args = event.text.split()[1:]
        action_id = os.urandom(8).hex()
        action_type = ""
        confirm_message = ""
        action_payload = {}

        if not args:
            # Interactive mode
            action_type = "addcache_interactive"
            confirm_message = "✨ You are about to enter **Interactive Add-Cache Mode**.\n\nSend me sticker packs (links, stickers, or emojis) one by one. I'll add them to the cache queue. Send /done when you're finished.\n\nAre you sure you want to begin?"
            action_payload = {}

        elif len(args) == 1 and args[0].lower() == 'all':
            action_type = "addcache_all"
            confirm_message = "🔄 This will queue **ALL** packs from the stats database that are not yet cached. This might be a very large number and take a long time.\n\nAre you sure?"
            action_payload = {}

        elif len(args) == 1 and args[0].isdigit():
            limit = int(args[0])
            action_type = "addcache_n"
            confirm_message = f"🔄 This will queue the top **{limit}** most popular packs from the stats database that are not yet cached.\n\nAre you sure?"
            action_payload = {"limit": limit}

        else: # Links
            pack_names = [extract_pack_name_from_url(link) for link in args]
            valid_packs = [name for name in pack_names if name]
            if not valid_packs:
                await event.reply("❌ No valid sticker/emoji pack links found.")
                return

            action_type = "addcache_links"
            confirm_message = f"🔄 This will queue **{len(valid_packs)}** specific pack(s) to be added to the cache (if not already present).\n\nAre you sure?"
            action_payload = {"pack_short_names": valid_packs}

        original_event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id, 'bot_reply_message_id': 0}
        self.ctx.pending_actions[action_id] = {"action_type": action_type, "payload": action_payload, "original_event_info": original_event_info}
        buttons = [
            [Button.inline("✅ Yes, Proceed", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await event.reply(confirm_message, buttons=buttons)
        raise StopPropagation

    async def _get_active_add_cache_session(self, user_id: int) -> Session | None:
        """Finds and returns add cache interactive session if exists else None."""
        user_flows = await session_manager.get_all_user_sessions(user_id)
        add_cache_sessions = user_flows.get(Flow.ADDCACHE.value)

        if add_cache_sessions:
            return add_cache_sessions[0]
        return None

    async def canceladdcache_command(self, event: events.NewMessage.Event):
        """Owner command to cancel an ongoing add-cache operation."""
        user_id = event.sender_id
        active_session = await self._get_active_add_cache_session(user_id)

        if not self.ctx.active_add_jobs and not active_session:
            await event.reply("✅ No active add-cache operation to cancel.")
            return
        
        # Handle cancelling the interactive mode
        if active_session:
            await session_manager.expire(user_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ Interactive add-cache mode has been cancelled.")

        if not self.ctx.active_add_jobs:
            return # No background jobs to cancel

        msg = await event.reply(f"Cancelling {len(self.ctx.active_add_jobs)} queued add-cache jobs...")

        cancelled_count = 0
        jobs_to_cancel = list(self.ctx.active_add_jobs)
        for log_id in jobs_to_cancel:
            if await queue_manager.cancel_item(user_id=SYSTEM_USER_ID, log_id=log_id):
                await db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
                cancelled_count += 1

        self.ctx.active_add_jobs.clear()

        if self.ctx.active_add_message:
            try:
                await self.ctx.client.edit_message(self.ctx.active_add_message.chat_id, self.ctx.active_add_message.id, "❌ Add-cache operation cancelled by user.")
                self.ctx.active_add_message = None
            except Exception:
                pass

        await msg.edit(f"✅ Cancelled **{cancelled_count}** pending jobs from the queue.")
        raise StopPropagation

    async def done_command(self, event: events.NewMessage.Event):
        """Owner command to exit interactive add-cache mode."""
        user_id = event.sender_id
        active_session = await self._get_active_add_cache_session(user_id)
        if active_session:
            await session_manager.expire(user_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ **Finished!** Exited interactive add-cache mode.")
        else:
            await event.reply("✅ You are not in an active interactive mode.")
        # Silently ignore if not in the correct state
        raise StopPropagation

    async def getjunk_command(self, event: events.NewMessage.Event):
        """Owner command to get a list of all junk files."""
        junk_records = await db.get_all_junk_files_grouped()
        
        if not junk_records:
            await event.reply("✅ No junk files found in the database. All clear!")
            return

        junk_data = {str(record['channel_id']): record['message_ids'] for record in junk_records}
        total_files = sum(len(ids) for ids in junk_data.values())

        try:
            file_path = os.path.join(TEMP_DIR, "junk_files.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(junk_data, f, indent=4)

            await event.reply(
                f"📄 Found a total of **{total_files}** junk files across **{len(junk_data)}** channels.\n\n"
                "The list has been sent as a JSON file. Use this to manually delete them with a userbot.",
                file=file_path
            )
        except Exception as e:
            logger.error(f"Failed to send junk files list as a JSON file. Error: {e}")
            await event.reply(f"**An error has occurred:**\n\n```{e}```")
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.error(f"Error removing junk files list: {e}")
        raise StopPropagation

    async def clearjunk_command(self, event: events.NewMessage.Event):
        """Owner command to clear all junk file entries from the database."""
        # We need to get the count for the confirmation message
        all_junk = await db.get_all_junk_files_grouped()
        if not all_junk:
            await event.reply("✅ The junk file log is already empty. Nothing to do!")
            return

        total_files = sum(len(record['message_ids']) for record in all_junk)

        action_id = os.urandom(8).hex()
        self.ctx.pending_actions[action_id] = {"action_type": "clearjunk"}

        buttons = [
            [Button.inline("✅ Yes, Clear DB Entries", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]

        await event.reply(
            f"🗑️ **Confirm Junk Log Deletion**\n\n"
            f"This will remove **{total_files}** file records from the `junk_files` table. "
            "This action **DOES NOT** delete the files from Telegram.\n\n"
            "**Only proceed if you have already manually deleted these files.** This action cannot be undone.",
            buttons=buttons
        )
        raise StopPropagation

    async def refund_command(self, event: events.NewMessage.Event):
        """Admin command to refund a Star payment."""
        # Only the owner should be able to do this
        if event.sender_id != OWNER_ID:
            return

        # Usage: /refund <charge_id> <user_id> [deduct] [no-db]
        args = event.message.text.split()
        if len(args) < 3:
            await event.reply("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Usage: <code>/refund &lt;charge_id&gt; &lt;user_id&gt; [deduct] [no-db]</code>", parse_mode="html")
            return

        try:
            target_user_id = int(args[2])
        except (ValueError, IndexError):
            await event.reply("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Usage: <code>/refund &lt;charge_id&gt; &lt;user_id&gt; [deduct] [no-db]</code>", parse_mode="html")
            return

        charge_id = args[1]

        # if something happens wrong with database and we need to refund manually
        if "no-db" in args:
            result = await self.ctx.payment_manager.refund(target_user_id, event.sender_id, charge_id, "admin", event.sender_id, "refunded by admin", False, True)
            if result.get('refund_success', False):
                await self.ctx.client.send_message(target_user_id, f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> <b>Refund Successful!</b> Stars have been returned to your account.", parse_mode='html')
                await event.reply(f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> <b>Refund Successful!</b> Stars have been returned to the user <b>{target_user_id}</b>.", parse_mode='html')
            else:
                await event.reply(f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> <b>Failed to refund stars for user {target_user_id} with charge ID {charge_id}</b>: <pre>{str(result.get('refund_error', 'Unknown error'))}</pre>", parse_mode='html')
            raise StopPropagation
        
        # normal refund
        message_text = ""
        try:
            result = await self.ctx.payment_manager.refund(target_user_id, event.sender_id, charge_id, "admin", event.sender_id, "refunded by admin", "deduct" in args, False)
            payment = result['payment']
            amount = payment['amount']
            
            # if refund succeeded
            if result['refund_success']:
                message_text += f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> <b>Refund Successful!</b> <b>{amount}</b> <tg-emoji emoji-id='5954135079662916434'>⭐</tg-emoji> have been returned to the user <b>{target_user_id}</b>.\n\n"
                await self.ctx.client.send_message(target_user_id, f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> <b>Refund Successful!</b> We have returned <b>{amount}</b> <tg-emoji emoji-id='5954135079662916434'>⭐</tg-emoji> to your account.", parse_mode='html')
                
                # if status update failed
                if not result['status_updated']:
                    message_text += f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Failed to mark payment as refunded for user {target_user_id} with charge ID <code>{charge_id}</code>: <pre>{result.get('status_update_error', 'Unknown error')}</pre>\n\n"

                # if deduction succeeded (in case requested)
                deduction_info = result['deduction_info']
                if deduction_info:
                    if deduction_info['action'] == 'deducted':
                        new_expiry_date = deduction_info['new_expiry_date']
                        message_text += f"<tg-emoji emoji-id='6296367896398399651'>✅</tg-emoji> Premium access was deducted from user {target_user_id}. New expiry date: <code>{new_expiry_date}</code>\n\n"
                    elif deduction_info['action'] == 'removed':
                        message_text += f"<tg-emoji emoji-id='6296367896398399651'>✅</tg-emoji> Premium access was deducted from user {target_user_id}. Since user had premium duration left less than {payment['duration_days']} days, user is no longer premium.\n\n"
                    else:
                        error = deduction_info.get('error', 'Unknown error')
                        message_text += f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Failed to deduct premium for user {target_user_id} with charge ID {charge_id}: <pre>{str(error)}</pre>\n\n"
            
            # if refund failed
            else:
                message_text += f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> <b>Failed to refund stars for user {target_user_id} with charge ID <code>{charge_id}</code>:</b> <pre>{result.get('refund_error', 'Unknown error')}</pre>"

        except ValueError as e:
            error = str(e)
            if error == "payment_not_found":
                await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Payment <b>not found</b>!", parse_mode='html')
            elif error == "payment_not_success":
                await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Payment is <b>not successful or already refunded</b>, cannot refund!", parse_mode='html')
            elif error == "user_id_mismatch":
                await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Payment is not for this user!", parse_mode='html')
            else:
                await event.reply(f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> <b>Failed to refund stars for user {target_user_id} with charge ID {charge_id}</b>: <pre>{error}</pre>", parse_mode='html')
            raise StopPropagation
        except Exception as e:
            logger.error(f"Error refunding stars for user {target_user_id} with charge ID {charge_id}: {e}")
            await event.reply(f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> <b>Failed to refund stars for user {target_user_id} with charge ID {charge_id}</b>: <pre>{str(e)}</pre>", parse_mode='html')
            raise StopPropagation

        await event.reply(message_text, parse_mode='html')        
        raise StopPropagation
