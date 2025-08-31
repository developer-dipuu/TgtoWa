"""
Telegram bot handlers for the TG Sticker/Emoji to WA Sticker Converter Bot
"""

import os 
import glob
import time
import zipfile
import asyncio
import logging
import re
import html
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, Button
from telethon.errors import UserIsBlockedError, ChatAdminRequiredError
from telethon.errors.rpcerrorlist import UserNotParticipantError
from telethon.events import StopPropagation
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import DocumentAttributeSticker, DocumentAttributeCustomEmoji, Message, ReactionEmoji
from typing import Optional, Sequence, List, Dict, Any

from config import *
from utils import *
from queue_manager import queue_manager, SYSTEM_PRIORITY, REGULAR_USER_PRIORITY, PREMIUM_USER_PRIORITY
from sticker_converter import StickerConverter
from session_manager import SessionManager, Flow, Session
import database as db
from notification_manager import NotificationManager
from utils import BackupManager

SYSTEM_USER_ID = 0
logger = logging.getLogger(__name__)

class BotHandlers:
    def __init__(self, client: TelegramClient, bot_info, notification_manager: NotificationManager):
        """
        Initializes the bot handlers with the Telethon client and other necessary components.
        """
        ensure_directories()
        self.client = client
        self.network_task = NetworkTask(self.client)
        self.converter = StickerConverter(self.client)
        self.session_manager = SessionManager()
        self.notification_manager = notification_manager
        self.backup_manager = BackupManager(self.client)
        self.processing_lock = asyncio.Lock()
        self.bot_username = f"@{bot_info.username}"
        self.cache_enabled = CACHE_ENABLED
        self.user_callback_locks = {}
        self.user_callback_locks_lock = asyncio.Lock()
        self._user_processing_lock = asyncio.Lock()
        self._users_adding_to_queue = set()
        self.reply_locks = {}
        self.reply_locks_lock = asyncio.Lock()
        self.pending_actions = {}
        self.active_refresh_jobs = set()
        self.active_refresh_message = None
        self.active_add_jobs = set()
        self.active_add_message = None
        self.daily_popular_packs = []
        self.all_time_popular_packs = []
        # formatted start message
        self.START_MESSAGE = START_MESSAGE_FORMAT.format(
            bot_username=self.bot_username,
        )
        #background tasks for cleanup
        asyncio.create_task(self._reply_locks_cleanup_loop(ttl_seconds=3600))
        asyncio.create_task(self._session_cleanup_loop(check_interval_seconds=600))
        asyncio.create_task(self._premium_users_cleanup_loop(check_interval_seconds=86400))
        asyncio.create_task(self._calculate_popular_packs_loop())
        asyncio.create_task(self._callback_locks_cleanup_loop(ttl_seconds=3600, check_interval_seconds=600))
        asyncio.create_task(self._daily_backup_loop())
        
    def check_banned(func):
        """Decorator to check if a user is banned before executing a command."""
        async def wrapper(self, event):
            if db.is_banned(event.sender_id):
                logger.warning(f"Banned user {event.sender_id} tried to use the bot.")
                raise StopPropagation # Ignore
            return await func(self, event)
        return wrapper

    def register_handlers(self):
        """
        Registers all event handlers with the Telethon client.
        """
        # user commands
        self.client.add_event_handler(self.start_command, events.NewMessage(pattern='/start', func=lambda e: e.is_private))
        self.client.add_event_handler(self.help_command, events.NewMessage(pattern='/help', func=lambda e: e.is_private))
        self.client.add_event_handler(self.queue_command, events.NewMessage(pattern='/queue', func=lambda e: e.is_private))
        self.client.add_event_handler(self.mystats_command, events.NewMessage(pattern='/mystats', func=lambda e: e.is_private))
        self.client.add_event_handler(self.premium_command, events.NewMessage(pattern='/premium', func=lambda e: e.is_private))
        self.client.add_event_handler(self.commands_command, events.NewMessage(pattern='/commands', func=lambda e: e.is_private))
        self.client.add_event_handler(self.suggest_command, events.NewMessage(pattern='/suggest', func=lambda e: e.is_private))
        self.client.add_event_handler(self.contact_command, events.NewMessage(pattern='/contact', func=lambda e: e.is_private))
        # owner commands
        self.client.add_event_handler(self.promote_command, events.NewMessage(pattern=r'/promote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.demote_command, events.NewMessage(pattern=r'/demote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.broadcast_command, events.NewMessage(pattern=r'/broadcast(?:$|\s.*)', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.broadcast_command, events.NewMessage(pattern=r'/broadcast@' + self.bot_username.lstrip('@') + r'(?:$|\s.*)', func=lambda e: not e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.send_command, events.NewMessage(pattern=r'/send(?:$|\s.*)', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.gstats_command, events.NewMessage(pattern=r'/gstats', func=lambda e: db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.getdb_command, events.NewMessage(pattern='/getdb', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.getlogs_command, events.NewMessage(pattern='/getlogs', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.toggle_cache_command, events.NewMessage(pattern='/togglecache', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.clearcache_command, events.NewMessage(pattern=r'/clearcache', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.refreshcache_command, events.NewMessage(pattern=r'/refreshcache', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.cancelrefresh_command, events.NewMessage(pattern=r'/cancelrefresh', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.addcache_command, events.NewMessage(pattern=r'/addcache', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.canceladdcache_command, events.NewMessage(pattern=r'/canceladdcache', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.done_command, events.NewMessage(pattern=r'/done', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        # Premium commands (admin use)
        self.client.add_event_handler(self.add_premium_command, events.NewMessage(pattern=r'/addpremium(?:@\w+)?(?:\s+([@\w\d]+))?(?:\s+(\d+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.remove_premium_command, events.NewMessage(pattern=r'/removepremium(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.extend_premium_command, events.NewMessage(pattern=r'/extendpremium(?:@\w+)?(?:\s+([@\w\d]+))?(?:\s+(\d+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.deduct_premium_command, events.NewMessage(pattern=r'/deductpremium(?:@\w+)?(?:\s+([@\w\d]+))?(?:\s+(\d+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.getstats_command, events.NewMessage(pattern=r'/getstats(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        # ban/unban (admin use)
        self.client.add_event_handler(self.ban_command, events.NewMessage(pattern=r'/ban', func=lambda e: e.is_private))
        self.client.add_event_handler(self.sban_command, events.NewMessage(pattern=r'/sban', func=lambda e: e.is_private))
        self.client.add_event_handler(self.unban_command, events.NewMessage(pattern=r'/unban', func=lambda e: e.is_private))

        # Handle all other private messages
        self.client.add_event_handler(self.handle_message, events.NewMessage(func=lambda e: e.is_private and (not e.text.startswith('/') )))
        self.client.add_event_handler(self.handle_callback_query, events.CallbackQuery())

    # ------- Clean up task loops --------
        
    async def _callback_locks_cleanup_loop(self, ttl_seconds: int = 3600, check_interval_seconds: int = 600):
        """Periodically clean old user callback locks to prevent memory growth."""
        while True:
            await asyncio.sleep(check_interval_seconds)
            try:
                now = datetime.now(timezone.utc)
                async with self.user_callback_locks_lock:
                    to_remove = []
                    for user_id, entry in self.user_callback_locks.items():
                        lock = entry.get("lock")
                        last_used = entry.get("last_used", now)
                        # Only remove if it's unlocked and hasn't been used for a while
                        if not lock.locked() and (now - last_used).total_seconds() > ttl_seconds:
                            to_remove.append(user_id)
                    
                    for user_id in to_remove:
                        self.user_callback_locks.pop(user_id, None)
                        logger.debug(f"Cleaned up callback lock for user {user_id}")

            except Exception as e:
                logger.error(f"FATAL: The _callback_locks_cleanup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600) # time for developer to fix and stop the same error code running again otherwise it will keep sending damn notifications every 10m
            logger.info("Cleaned old user callback locks.")

    async def _reply_locks_cleanup_loop(self, ttl_seconds=3600):
        """Periodically clean old reply locks to prevent memory growth.
        Only remove locks that are unlocked AND not used for > ttl_seconds."""
        while True:
            await asyncio.sleep(ttl_seconds)
            logger.info("Cleaning old admin reply locks...")
            try:
                now = datetime.now(timezone.utc)
                async with self.reply_locks_lock:
                    to_remove = []
                    for cid, entry in list(self.reply_locks.items()):
                        lock = entry.get("lock")
                        last = entry.get("last_used", now)
                        # remove only if unlocked and idle longer than ttl_seconds
                        if (not lock.locked()) and ((now - last).total_seconds() > ttl_seconds):
                            to_remove.append(cid)
                    for cid in to_remove:
                        self.reply_locks.pop(cid, None)
            except Exception as e:
                logger.error(f"FATAL: The _reply_locks_cleanup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)
            logger.info("cleaned up old admin locks.")

    async def _session_cleanup_loop(self, check_interval_seconds: int = 600):
        """Periodically cleans up expired and old user sessions."""
        while True:
            await asyncio.sleep(check_interval_seconds)
            try:
                await self.session_manager.cleanup()
            except Exception as e:
                logger.error(f"FATAL: The _session_cleanup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)
            logger.info("Periodic session cleanup finished.")

    async def _premium_users_cleanup_loop(self, check_interval_seconds: int = 86400):
        """Periodically cleans up expired premium users from the database."""
        while True:
            # Wait for the next interval
            await asyncio.sleep(check_interval_seconds)
            try:
                logger.info("Running scheduled cleanup of expired premium users...")
                removed_count = db.remove_expired_premium_users()
                if removed_count > 0:
                    logger.info(f"SYSTEM: Automatically removed {removed_count} expired premium users.")
                else:
                    logger.info("No expired premium users found to remove.")
            except Exception as e:
                logger.error(f"FATAL: The _premium_users_cleanup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)

    async def _refresh_popular_packs_cache(self):
        """Fetches popular packs from DB and loads them into memory."""
        try:
            self.daily_popular_packs = await asyncio.to_thread(db.get_popular_packs, 'daily')
            self.all_time_popular_packs = await asyncio.to_thread(db.get_popular_packs, 'all_time')
            logger.info(f"Popular packs refreshed. Loaded {len(self.daily_popular_packs)} daily and {len(self.all_time_popular_packs)} all-time packs.")
        except Exception as e:
            logger.error(f"Failed to refresh in-memory popular packs cache: {e}")
            await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )

    async def _daily_backup_loop(self):
        """Periodically runs the backup manager task at midnight."""
        while True:
            try:
                now = datetime.now(timezone.utc)
                next_run = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
                sleep_seconds = (next_run - now).total_seconds()

                logger.info(f"Next daily backup scheduled in {(sleep_seconds / 3600):.2f} hours.")
                await asyncio.sleep(sleep_seconds)

                logger.info("Starting scheduled daily backup...")
                await self.backup_manager.run_backup()

            except Exception as e:
                logger.error(f"FATAL: The _daily_backup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)

    async def _calculate_popular_packs_loop(self):
        """Periodically calculates and stores popular packs."""
        # Run once on startup to ensure data is available immediately
        try:
            await asyncio.to_thread(db.calculate_and_store_popular_packs)
            await self._refresh_popular_packs_cache()
        except Exception as e:
            logger.error(f"Initial popular packs calculation failed: {e}")

        while True:
            try:
                now = datetime.now(timezone.utc)
                next_run = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
                sleep_seconds = (next_run - now).total_seconds()
                
                await asyncio.sleep(sleep_seconds)
                
                # Run the blocking DB function in a separate thread
                await asyncio.to_thread(db.calculate_and_store_popular_packs)
                await self._refresh_popular_packs_cache()
                logger.info(f"Popular packs refreshed.")

            except Exception as e:
                logger.error(f"FATAL: The _calculate_popular_packs_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)

    def _create_channel_join_buttons(self) -> list:
        """Dynamically creates buttons for channels and groups."""
        keyboard = []
        # iterate through the list of tuples from config.py
        for i in range(0, len(REQUIRED_CHANNELS_FORMATTED), 2):
            row = []

            # First Button in Row
            name1, link1 = REQUIRED_CHANNELS_FORMATTED[i][:2]
            row.append(Button.url(f"{name1}", url=link1))

            # Second Button in Row (if it exists)
            if i + 1 < len(REQUIRED_CHANNELS_FORMATTED):
                name2, link2 = REQUIRED_CHANNELS_FORMATTED[i+1][:2]
                row.append(Button.url(f"{name2}", url=link2))
            
            keyboard.append(row)
        
        keyboard.append([Button.inline("✅ Check Again", b"check_membership")])
        return keyboard
    
    async def react(self, event: events.NewMessage.Event| None = None, chat_id: int | None = None, msg_id: int | None = None, emoji: str = "👍", big: bool = False) -> bool:
        if not event and not (chat_id and msg_id):
            raise ValueError("You must provide either an event or both chat_id and msg_id")
        if event:
            chat_id=  event.chat_id
            msg_id = event._message_id
        try:
            await self.client(SendReactionRequest(
                peer=chat_id,
                big=big,
                msg_id=msg_id,
                reaction=[ReactionEmoji(
                    emoticon=emoji
                )]
            ))
        except Exception as e:
            logger.error(f"An error while reacting to message {msg_id} in chat {chat_id}: {e}")
            return False
        return True
    
    async def delete_cache(self, set_id):
        position = db.remove_from_cache(set_id) 
        if position:
            channel_id, message_ids = position
            try:
                await self.client.delete_messages(channel_id, message_ids)
            except ChatAdminRequiredError as e:
                logger.error(f"Can't delete cache messages {message_ids}! Insufficient permissions in the channel {channel_id}!")
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
            except Exception as e:
                logger.error(f"Could not delete cache messages {message_ids} in the channel {channel_id}. An error has occured: {e}")
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
        else:
            return None
        return True
    
    async def delete_multiple_cache(self, set_ids):
        messages_to_delete = {} # {channel1: [msg4,msg5,msg6], channel2: [msg10,msg11,msg12] }

        for set_id in set_ids:
            position = db.remove_from_cache(set_id)
            if position:
                channel_id, message_ids = position
                messages_to_delete.setdefault(channel_id, []).extend(message_ids)

        if not messages_to_delete:
            return None
        
        status = True
        while messages_to_delete:
            channel = list(messages_to_delete.keys())[0] # first key
            message_chunk = messages_to_delete.get(channel)[0:100] # collect upto 100 messages for a single channel
            messages_to_delete[channel] = messages_to_delete.get(channel)[100:] # removed the collected messages

            if not messages_to_delete.get(channel): # if all messages are collected for that cahnnel remove it from the dic
                messages_to_delete.pop(channel)

            try:
                await self.client.delete_messages(channel, message_chunk)
            except ChatAdminRequiredError as e:
                logger.error(f"Can't delete cache messages {message_chunk}! Insufficient permissions in the channel {channel}!")
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel, message_chunk, e))
                status = False
            except Exception as e:
                logger.error(f"Could not delete cache messages {message_chunk} in the channel {channel}.Error: {e}")
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel, message_chunk, e))
                status = False
            await asyncio.sleep(1)

        return status
    
    async def delete_messages(self, chat_id: int, msg_id: int | Sequence[int], custom_error_log: str | None = None):
        """Deletes messages from a chat. Accepts a single message ID or list of message IDs. Returns success."""
        def log_error(base_msg: str):
            full_msg = f"{custom_error_log} Error: {base_msg}" if custom_error_log else base_msg
            logger.error(full_msg)

        try:
            await self.client.delete_messages(chat_id, msg_id)
        except ChatAdminRequiredError as e:
            log_error(f"Can't delete message(s): {msg_id}. Bot lacks required permissions in chat {chat_id}.")
            if custom_error_log:
                asyncio.create_task(self.notification_manager.send_message_delete_failure(chat_id, msg_id, custom_error_log, e))
            return False
        except Exception as e:
            log_error(f"Could not delete message(s) {msg_id} in chat {chat_id}. Error: {e}")
            if custom_error_log:
                asyncio.create_task(self.notification_manager.send_message_delete_failure(chat_id, msg_id, custom_error_log, e))
            return False
        return True
        
    async def delete_multiple_messages(self, chat_id: int, message_ids: List[int], custom_error_log: str | None = None):
        """Deletes bulk messages from a chat with proper waiting to avoid rate limits and with max speed. Accepts a list of message IDs. Returns success."""
        if not message_ids:
            return None
        
        def log_error(base_msg: str):
            full_msg = f"{custom_error_log} Error: {base_msg}" if custom_error_log else base_msg
            logger.error(full_msg)

        status = True
        while message_ids:
            message_chunk = message_ids[0:100] # collect upto 100 messages
            message_ids = message_ids[100:] # removed the collected messages

            try:
                await self.client.delete_messages(chat_id, message_chunk)
            except ChatAdminRequiredError as e:
                log_error(f"Can't delete messages {message_chunk}. Bot lacks required permissions in chat {chat_id}!")
                if custom_error_log:
                    asyncio.create_task(self.notification_manager.send_message_delete_failure(chat_id, message_chunk, custom_error_log, e))
                return False # no need to try for other chunks it wont work
            except Exception as e:
                log_error(f"Could not delete messages {message_chunk} in chat {chat_id}. Error: {e}")
                if custom_error_log:
                    asyncio.create_task(self.notification_manager.send_message_delete_failure(chat_id, message_chunk, custom_error_log, e))
                status = False
            await asyncio.sleep(1) # rate limits

        return status

    async def check_user_membership(self, user_id: int) -> bool:
        """Check if user is a member of required channels."""
        if not REQUIRED_CHANNELS_FORMATTED:
            return True
        try:
            # iterate through the list of tuples ("Name", "link")
            for element in REQUIRED_CHANNELS_FORMATTED:
                # only valid format lenths are allowed
                if len(element)==2:
                    name, link = element
                elif len(element)==3:
                    name = element[0]
                    link = element[2]
                else:
                    continue

                try:
                    # Use the link for the check
                    await self.client(GetParticipantRequest(channel=link, participant=user_id))
                except UserNotParticipantError:
                    logger.warning(f"User {user_id} is not a participant in {name}.")
                    return False
                except Exception as e:
                    # cases where the link is invalid or the bot isn't an admin
                    logger.error(f"Could not check membership for user {user_id} in {name}: {e}")
                    return False
            return True
        except Exception as e:
            logger.error(f"General error in check_user_membership for user {user_id}: {e}")
            return False

    async def check_cache(self, event, sticker_set, log_id: Optional[int] = None) -> bool:
        """Checks if the sticker set is cached or not if cached it will directly send those files and return True,
        else it will handle cache inconsistencies if any and return False"""
        user_id = event.sender_id
        set_id = sticker_set.set.id
        current_title = sticker_set.set.title
        current_sticker_count = len(sticker_set.documents)

        # Check if the pack is cached and up-to-date
        cache_status, channel_id, message_ids = db.is_pack_cached(set_id, current_title, current_sticker_count)
        
        # --- hehe cache hit ---
        if cache_status == 'hit':
            # Verify the cached files actually exist
            if None not in await self.client.get_messages(channel_id, ids=message_ids):
                logger.info(f"✅ Cache hit for pack {set_id} in channel {channel_id}. Forwarding to user {user_id}.")
                num_packs = len(message_ids)
                
                # We need to log this as a successful conversion even though its from cache
                is_emoji_pack = sticker_set.set.emojis
                pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
                pack_url = f"https://t.me/{pack_type_url}/{sticker_set.set.short_name}"
                if log_id is None:
                    log_id = db.log_conversion_request(user_id, sticker_set.set.id, pack_url, is_emoji_pack)
                
                await event.reply(f"✅ Found this pack in the cache! Sending **{num_packs}** file(s) instantly...")

                try:
                    messages = await self.client.get_messages(channel_id, ids=message_ids)
                    for message in messages:
                        await self.client.send_message(entity=event.chat_id, message=message, link_preview=False)

                    logger.info(f"✅ Successfully forwarded pack {set_id} from cache to user {user_id}.")
                    await self.client.send_message(event.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")
                    db.update_conversion_log(log_id, "completed_from_cache", datetime.now(timezone.utc), 0.0)
                    return True
                except UserIsBlockedError:
                    # some dumbass block the bot even when it is sending files
                    logger.error(f"User has blocked the bot! Failed to forward cached messages for pack {set_id} to user {user_id}.")
                    db.update_conversion_log(log_id, "completed_from_cache_but_blocked", datetime.now(timezone.utc), 0.0)
                    return True
                # if all successful upload
                except Exception as e:
                    logger.error(f"Failed to forward cached messages for pack {set_id} to user {user_id}: {e}")
                    # If forwarding fails, it's a critical error. Let's treat it as a cache miss and re-convert.
                    await event.reply("🤔 Oops! I found this in the cache, but couldn't send it. I'll try re-converting it for you now.")
                    db.update_conversion_log(log_id, "failed_forward_from_cache", datetime.now(timezone.utc), 0.0)
                    # clear the broken cache
                    asyncio.create_task(self.delete_cache(set_id))
            else:
                # The DB has an entry, but the messaages are missing or deletd!
                logger.error(f"Cache inconsistency! Files for pack {set_id} not found in cahnnel {channel_id}. Removing DB entry.")
                # clear the broken cache
                asyncio.create_task(self.delete_cache(set_id))
        # --- stale cache T~T ---
        elif cache_status == 'stale':
            logger.warning(f"Stale cache found for pack {set_id}. Deleting old cache before re-converting.")
            asyncio.create_task(self.delete_cache(set_id))

        return False # for cache miss or stale cache or inconsistent cache files 

    def _get_message_content_for_db(self, message: Message) -> str:
        """Extracts text or a placeholder from a message for DB logging."""
        if message.text:
            return message.text
        elif message.sticker:
            # Try to get the emoji associated with the sticker
            emoji_alt = next((attr.alt for attr in message.sticker.attributes if isinstance(attr, DocumentAttributeSticker)), "")
            return f"[sticker: {emoji_alt}]".strip()
        elif message.photo:
            return "[photo]"
        elif message.video:
            return "[video]"
        elif message.document:
            return f"[document: {message.document.mime_type}]"
        else:
            return "[unsupported media]"
        
    async def _get_active_input_sessions(self, user_id: int) -> List[Session]:
        """Finds all active sessions for a user that are awaiting text input."""
        active_sessions_with_flow = []
        INPUT_AWAITING_STATES = {
            'awaiting_contact_message',
            'awaiting_custom_title',
            'awaiting_custom_author',
            'awaiting_addcache_input'
        }

        user_flows = await self.session_manager.get_all_user_sessions(user_id)
        for flow_val, sessions in user_flows.items():
            try:
                flow = Flow(flow_val) # Convert string from dict key back to Enum
                for session in sessions.values():
                    # We check if the session is active and its state requires input
                    if session.active and session.state in INPUT_AWAITING_STATES:
                        active_sessions_with_flow.append((session, flow))
            except ValueError:
                # This could happen if a Flow is removed from the Enum but still exists in the store
                logger.warning(f"Found session with unknown flow '{flow_val}' for user {user_id}")

        return active_sessions_with_flow
    
    async def _prompt_for_ambiguous_input(self, event: events.NewMessage.Event, sessions_with_flow: List[tuple[Session, Flow]]):
        """Notifies the user that their input is ambiguous and provides option to cancel."""

        msg_to_del = [event._message_id]

        # All sessions share the same ambiguity prompt, so we only need to check the first one.
        first_session, _ = sessions_with_flow[0]
        old_prompt_id = first_session.payload.get('ambiguity_prompt_id')
        if old_prompt_id: msg_to_del .append(old_prompt_id)

        asyncio.create_task(self.delete_messages(
            event.chat_id,
            msg_to_del,
            custom_error_log="Failed to delete old ambiguity prompt."
        ))

        text = (
            "🤔 **Multiple Actions Pending**\n\n"
            "You have several actions waiting for your text input. "
            "To continue, please **scroll up and reply directly** to the correct prompt message.\n\n"
            "Here are your pending actions:"
        )
        
        action_list = []
        buttons = []

        for session, flow in sessions_with_flow:
            payload = session.payload
            sid = session.session_id

            action_desc = "Unknown Action"
            if flow == Flow.CUSTOMIZE:
                pack_title = payload['sticker_set'].set.title
                if session.state == 'awaiting_custom_title':
                    action_desc = f"✏️ Set Title for '{pack_title[:20]}...'"
                elif session.state == 'awaiting_custom_author':
                    action_desc = f"👤 Set Author for '{pack_title[:20]}...'"
            elif flow == Flow.CONTACT:
                action_desc = "✉️ Send Contact Message"

            action_list.append(f"• {action_desc}")
            buttons.append([Button.inline(f"❌ Cancel: {action_desc}", f"cancel_session_{flow.value}_{sid}")])
        
        buttons.append([Button.inline("🚫 Cancel All Pending Actions", "cancel_all_input_sessions")])
    
        full_text = text + "\n" + "\n".join(action_list)
        prompt_msg = await event.respond(full_text, buttons=buttons)

        prompt_id = prompt_msg.id
        
        # Tag all the ambiguous sessions with the ID of the prompt we just sent
        for session, flow in sessions_with_flow:
            await self.session_manager.update(
                event.sender_id,
                flow,
                session.session_id,
                payload_mutator=lambda p: p.update({'ambiguity_prompt_id': prompt_id})
            )

        raise StopPropagation

    async def _process_session_input(self, event: events.NewMessage.Event, session: Session, flow: Flow):
        """Routes a user's text message to the correct logic based on the session."""
        user_id = event.sender_id

        prompt_id_to_delete = session.payload.get('ambiguity_prompt_id')

        if prompt_id_to_delete:
            asyncio.create_task(self.delete_messages(event.chat_id, prompt_id_to_delete, "Failed to delete ambagious prompt."))
            
            # Clean the ambiguity_prompt_id from all active sessions for this user
            all_active = await self._get_active_input_sessions(user_id)
            for active_session, active_flow in all_active:
                if 'ambiguity_prompt_id' in active_session.payload:
                    await self.session_manager.update(
                        user_id,
                        active_flow,
                        active_session.session_id,
                        payload_mutator=lambda p: p.pop('ambiguity_prompt_id', None)
                    )

        # --- CONTACT MESSAGE ---
        if flow == Flow.CONTACT and session.state == 'awaiting_contact_message':
            await self.session_manager.expire(user_id, Flow.CONTACT, session.session_id) # Expire after use

            message_content = self._get_message_content_for_db(event.message)
            contact_id = db.log_contact_message(user_id, event.message.id, message_content)
            admin_ids = db.get_all_admin_ids()

            user = await event.get_sender()
            user_display_name = get_user_display_name(user)
            role = "⭐ Premium User" if db.is_premium(user.id) else "👤 Regular User"
            stats = db.get_user_stats(user.id)

            header_message = CONTACT_ADMIN_NOTIFICATION_HEADER.format(
                contact_id=contact_id, 
                user_display_name=html.escape(user_display_name),
                user_id=user.id, 
                role=role, 
                succeeded=stats['succeeded'],
                failed=stats['failed'], 
                cancelled=stats['cancelled'], 
                total=stats['total']
            )

            for admin_id in admin_ids:
                try:
                    await self.client.send_message(admin_id, header_message, parse_mode='html')
                    await self.client.forward_messages(admin_id, event.message)
                    logger.debug(f"Forwarded the {user.id} user's contact message to the admin {admin_id}")
                except Exception as e:
                    logger.warning(f"Failed to forward contact message to admin {admin_id}: {e}")

            await event.reply(CONTACT_SUCCESS_MESSAGE, parse_mode='html')
            raise StopPropagation

        # --- CUSTOMIZATION INPUT ---
        elif flow == Flow.CUSTOMIZE and session.state in ('awaiting_custom_title', 'awaiting_custom_author'):
            payload = session.payload

            if not event.text or not event.text.strip():
                await event.delete()
                msg = await event.respond("⚠️ Only valid **text messages** are allowed. Please try again.")
                payload['failed_inputs'].append(msg.id)
                await self.session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, payload_mutator=lambda p: p.update(payload))
                return

            user_input = event.text.strip()

            if session.state == 'awaiting_custom_title':
                if len(user_input) > 50:
                    await event.delete()
                    msg = await event.respond("⚠️ Title too long (max 50 chars). Please try again.")
                    payload['failed_inputs'].append(msg.id)
                    await self.session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, payload_mutator=lambda p: p.update(payload))
                    return
                payload['custom_title'] = user_input

            elif session.state == 'awaiting_custom_author':
                if len(user_input) > 30:
                    await event.delete()
                    msg = await event.respond("⚠️ Author too long (max 30 chars). Please try again.")
                    payload['failed_inputs'].append(msg.id)
                    await self.session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, payload_mutator=lambda p: p.update(payload))
                    return
                payload['custom_author'] = user_input

            await self.react(event, emoji= "🆒", big=True)
            messages_to_delete = payload.get("failed_inputs", [])
            payload["failed_inputs"] = []

            session.state = 'awaiting_customization_choice' # Go back to the main menu
            await self.session_manager.update(
                user_id, Flow.CUSTOMIZE, session.session_id,
                state='awaiting_customization_choice',
                payload_mutator=lambda p: p.update(payload),
                ttl_seconds=3600
            )
            await self._update_customization_prompt(user_id, session)
            asyncio.create_task(self.delete_multiple_messages(event.chat_id, messages_to_delete, "Failed to delete invalid customization input messages."))
            raise StopPropagation

        # --- ADD CACHE INPUT ---
        elif flow == Flow.ADDCACHE and session.state == 'awaiting_addcache_input':
            await self._execute_interactive_addcache(event)
            raise StopPropagation

    async def _execute_interactive_addcache(self, event: events.NewMessage.Event):
        """Handles a single pack submission in interactive add-cache mode."""
        pack_input = None
        if event.text:
            pack_input = extract_pack_name_from_url(event.text)
        elif event.sticker:
            for attr in event.sticker.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    pack_input = attr.stickerset
                    break
        elif event.document and hasattr(event.document, 'attributes'):
            for attr in event.document.attributes:
                if isinstance(attr, DocumentAttributeCustomEmoji):
                    pack_input = attr.stickerset
                    break
        
        if not pack_input:
            await event.reply("❌ Invalid input. Please send a sticker/emoji pack link, or a sticker/emoji from the pack.")
            return

        try:
            sticker_set = await self.network_task.get_sticker_set(pack_input)
            if not sticker_set or not sticker_set.documents:
                await event.reply("❌ Couldn't find that sticker pack. It might be private or empty.")
                return

            # Perform a silent cache check
            set_id = sticker_set.set.id
            set_title = sticker_set.set.title
            set_count = len(sticker_set.documents)

            cache_status, channel_id, message_ids = db.is_pack_cached(set_id, set_title, set_count, is_system_process=True)

            if cache_status == 'hit':
                messages = await self.client.get_messages(channel_id, ids=message_ids)
                if messages and all(msg is not None for msg in messages):
                    await event.reply(f"✅ Pack '{set_title}' is already in the cache. Skipped.")
                    return
                else:
                    asyncio.create_task(self.delete_cache(set_id)) # Inconsistent cache
            elif cache_status == 'stale':
                asyncio.create_task(self.delete_cache(set_id))
            
            # Queue it
            placeholder = await event.reply(f"✅ Adding '{set_title}' to the queue...")
            system_id = SYSTEM_USER_ID
            estimated_seconds = estimate_wait_time(sticker_set.documents, None)
            is_emoji = sticker_set.set.emojis
            pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{sticker_set.set.short_name}"
            log_id = db.log_conversion_request(system_id, set_id, pack_url, is_emoji)
            
            position = await queue_manager.add_to_queue(
                user_id=system_id, username="System AddCache (Interactive)", bot_reply_message_id=placeholder.id,
                sticker_set=sticker_set, estimated_seconds=estimated_seconds, log_id=log_id,
                priority=SYSTEM_PRIORITY, event=event, is_cache_suspicious=False,
                is_silent_mode=True
            )
            self.active_add_jobs.add(log_id)
            await placeholder.edit(f"✅ Queued '{set_title}' for caching at position {position}.")
            
            if not self.processing_lock.locked():
                if not queue_manager.get_queue_stats()["currently_processing"]:
                    asyncio.create_task(self.process_queue())

        except Exception as e:
            await event.reply(f"❌ An error occurred: {e}")
            logger.error(f"Interactive AddCache Error: {e}", exc_info=True)

    async def _start_customization_flow(self, event: events.NewMessage.Event, sticker_set):
        """Sends the initial customization prompt to premium users."""
        user_id = event.sender_id
        
        payload = {
            "sticker_set": sticker_set,
            "original_event": event,
            "prompt_message_id": None,
            "custom_title": None,
            "custom_author": None,
            "failed_inputs": []
        }
        session = await self.session_manager.create(
            user_id=user_id,
            flow=Flow.CUSTOMIZE,
            state="awaiting_customization_choice",
            payload=payload,
            ttl_seconds=3600 # 1 hour to decide
        )
        await self._update_customization_prompt(user_id, session)

    async def _update_customization_prompt(self, user_id: int, session: Session):
        """Edits the prompt message with the current customization state and buttons."""
        if not session or not session.active:
            return

        payload = session.payload
        event = payload['original_event']
        title = html.escape(payload['custom_title'] or payload['sticker_set'].set.title)
        author = html.escape(payload['custom_author'] or self.bot_username)
        
        text = (
            f"✨ <b>Premium Customization</b> ✨\n\n"
            f"Here's the current setup for your pack:\n"
            f"<blockquote>- <b>Title</b>: <code>{title}</code></blockquote>\n"
            f"<blockquote>- <b>Author</b>: <code>{author}</code></blockquote>\n"
            f"Ready to go, or want to make a change?"
        )
        
        sid = session.session_id
        buttons = [
            [Button.inline("✏️ Set Title", f"customize_title_{sid}"), Button.inline("👤 Set Author", f"customize_author_{sid}")],
            [Button.inline("🚀 Convert Now", f"customize_convert_{sid}")],
            [Button.inline("❌ Cancel", f"customize_cancel_{sid}")]
        ]
        
        try:
            if not payload['prompt_message_id']:
                bot_message = await event.reply(
                    text,
                    buttons=buttons,
                    parse_mode='html'
                )
                payload['prompt_message_id'] = bot_message.id
                await self.session_manager.update(user_id, Flow.CUSTOMIZE, sid, payload_mutator=lambda p: p.update(payload))
            else:
                await self.client.edit_message(
                    event.chat_id,
                    payload['prompt_message_id'],
                    text,
                    buttons=buttons,
                    parse_mode='html'
                )
        except Exception as e:
            logger.warning(f"Failed to send customization prompt to user {user_id}: {e}")


    async def _queue_sticker_pack(self, event, sticker_set, is_premium, custom_title: Optional[str] = None, custom_author: Optional[str] = None):
        """Helper function to consolidate the logic for adding a pack to the queue."""
        user = await event.get_sender()

        # find estimated time and user priority
        estimated_seconds = estimate_wait_time(sticker_set.documents, None)
        priority = PREMIUM_USER_PRIORITY if is_premium else REGULAR_USER_PRIORITY
        # max conversion duration cap
        if not is_premium:
            if estimated_seconds > MAX_CONVERSION_SECONDS_REGULAR:
                await event.reply(
                    (
                        "😟 **Pack Too Large for Regular Users!**\n\n"
                        f"This pack is estimated to take more than **{MAX_CONVERSION_SECONDS_REGULAR // 60} minutes** to convert, "
                        "which exceeds the time limit for regular users.\n\n"
                        "Upgrade to **Premium** to convert larger packs instantly!\n"
                    ),
                    buttons=[[Button.inline("💎 Learn about Premium", b"premium")]]
                )
                return
            
        is_emoji_pack = sticker_set.set.emojis
        pack_display_name = sticker_set.set.title

        # get the user's name pack url
        user_display_name = get_user_display_name(user)
        pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
        pack_url = f"https://t.me/{pack_type_url}/{sticker_set.set.short_name}"

        # Log the request to the database
        log_id = db.log_conversion_request(user.id, sticker_set.set.id, pack_url, is_emoji_pack)

        # send adding to queue message
        placeholder_message = await event.reply("⌛ Adding to the queue...")

        # Determine if this pack is "cache suspicious"
        is_suspicious = not custom_title and not custom_author and self.cache_enabled and queue_manager.is_set_id_queued(sticker_set.set.id)
        if is_suspicious:
            logger.info(f"Queueing pack {sticker_set.set.id} as 'cache suspicious'.")


        # add to queue and get position for this item
        position = await queue_manager.add_to_queue(
                user_id=user.id,
                username=user_display_name,
                bot_reply_message_id=placeholder_message.id,
                sticker_set=sticker_set,
                estimated_seconds=estimated_seconds,
                log_id=log_id,
                priority=priority,
                event=event,
                is_cache_suspicious=is_suspicious,
                custom_title=custom_title,
                custom_author=custom_author
        )
        # detailed added to queue successful message string
        if position != 1:
            safe_pack_name = html.escape(pack_display_name)
            if is_premium:
                current_queue_count = await queue_manager.get_user_queue_count(user.id)
                slots_left = MAX_CONCURRENT_PREMIUM_REQUESTS - current_queue_count
                final_message_text = (f"<b>⭐ VIP Status Confirmed!</b>\n\n"
            f"Your pack: <b><a href=\"{pack_url}\">{safe_pack_name}</a></b> has been fast-tracked to position <b>{position}</b>.\n")

                if slots_left > 0:
                    final_message_text += f"<blockquote>As a premium user, you can still add <b>{slots_left}</b> more pack(s) to the queue. Keep 'em coming!</blockquote>\n"

                final_message_text += "\n<b>I'll notify you when the conversion starts!</b>"
            else:
                final_message_text = (f"<b>✅ Added to conversion queue!</b>\n\n"
                f"📦 Pack: <a href=\"{pack_url}\">{safe_pack_name}</a>\n📍 Position: {position}\n\n"
                f"<blockquote>I'll notify you when the conversion starts!</blockquote>")

            # finally edit the message with detailed one
            await self.client.edit_message(
                entity=placeholder_message.chat_id,
                message=placeholder_message.id,
                text=final_message_text,
                buttons=[[Button.inline("📊 Check Queue", b"check_queue")],[Button.inline("❌ Cancel", data=f"cancel_item_{log_id}".encode())]],
                link_preview=False, parse_mode='html'
            )

        if not self.processing_lock.locked():
            # Check if anyone is processing before starting a new process_queue task
            is_processing = queue_manager.get_queue_stats()["currently_processing"]
            if not is_processing:
                asyncio.create_task(self.process_queue())


    @check_banned
    async def handle_message(self, event: events.NewMessage.Event):
        """
        Handle incoming messages. This is the main router for non-command messages.
        It prioritizes session-based inputs before treating a message as a new conversion request.
        """
        user = await event.get_sender()

        # ------ handle admin replies -------
        if event.is_reply and db.is_admin(user.id):
            if await self.handle_admin_reply(event): # if it was handled
                raise StopPropagation 

        # ---------- handle session based iput -------
        session_from_reply = None
        flow_from_reply = None
        if event.is_reply:
            session_info = await self.session_manager.from_reply(event.chat_id, event.reply_to_msg_id)
            if session_info:
                uid, flow_val, sid = session_info
                flow_from_reply = Flow(flow_val) # convert back to Enum
                session_from_reply = await self.session_manager.get(uid, flow_from_reply, sid)

        if session_from_reply and session_from_reply.active:
            # User replied to a session message, process it directly
            await self._process_session_input(event, session_from_reply, flow_from_reply)
            return
        
        # if it wasnt a reply to a session message, check for any active input sessions
        active_sessions_with_flow = await self._get_active_input_sessions(user.id)

        if event.is_reply and len(active_sessions_with_flow) >= 1:
            # user replied to a wrong message or expired session
            await event.reply("The messsage you replied to is not a valid input action or has expired.")
            return
        
        if len(active_sessions_with_flow) == 1: # single input session
            session, flow = active_sessions_with_flow[0]
            await self._process_session_input(event, session, flow)
            return
        elif len(active_sessions_with_flow) > 1: # multiple input sessions for non replied msg aint allowed bro
            await self._prompt_for_ambiguous_input(event, active_sessions_with_flow)
            return
        
        # ----- fine its a normal conversion request lets procced --------------

        # update the database
        db.add_or_update_user(user.id, user.username, get_user_display_name(user))
        # membership check
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
            return
        
        # if not a text or sticker
        if not (event.text or event.sticker):
            await event.reply(
                    "❌ **Invalid input!**\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert."
                )
            return

        is_premium = db.is_premium(user.id)
        limit = MAX_CONCURRENT_PREMIUM_REQUESTS if is_premium else MAX_CONCURRENT_REGULAR_REQUESTS

        # max queue limit
        async with self._user_processing_lock:
            current_queue_count = await queue_manager.get_user_queue_count(user.id)
            realistic_position = current_queue_count + (1 if user.id in self._users_adding_to_queue else 0)
            if realistic_position >= limit:
                if is_premium:
                    message = (f"⏳ **You've reached your limit!**\n\n"
                            f"You currently have {realistic_position}/{limit} items in the queue. "
                            f"Please wait for one to complete before adding more.")
                else:
                    message = "⏳ You're already in the queue! Please wait for your current request to complete."

                asyncio.create_task(event.reply(message, buttons=[[Button.inline("📊 Check Queue", b"check_queue")]]))
                return
            
            # if check passes mark user as adding to queue
            self._users_adding_to_queue.add(user.id)
        
        try:        
            # now time to extract pack details based on the type of message sent
            pack_input = None
            
            if event.text:
                # if a text 
                pack_input = extract_pack_name_from_url(event.text)
                if not pack_input:
                    await event.reply(
                        "❌ **Invalid input!**\n\n"
                        "Please send a valid Telegram sticker or emoji pack link, "
                        "or forward a sticker/emoji from the pack you want to convert."
                    )
                    return

            elif event.sticker:
                # if its a sticker
                for attr in event.sticker.attributes:
                    if isinstance(attr, DocumentAttributeSticker):
                        pack_input = attr.stickerset
                        break
                
                if not pack_input:
                    await event.reply(
                        "❌ This sticker doesn't seem to belong to a pack I can access.\n\nPlease forward a sticker from a public sticker pack."
                    )
                    return

            elif event.document and hasattr(event.document, 'attributes'):
                # if anything else is sent check if its a custom emoji
                for attr in event.document.attributes:
                    if isinstance(attr, DocumentAttributeCustomEmoji):
                        pack_input = attr.stickerset
                        break
                
                if not pack_input:
                    # if that document aint an emoji
                    await event.reply(
                        "❌ **Invalid input!**\n\n"
                        "Please send a valid Telegram sticker or emoji pack link, "
                        "or forward a sticker/emoji from the pack you want to convert."
                    )
                    return
                
            # Fetch the sticker/emoji set to get its actual name and type
            try:
                sticker_set = await self.network_task.get_sticker_set(pack_input)

                if not sticker_set or not sticker_set.documents:
                    logger.error(f"Could not fetch a valid sticker set for input: {pack_input}")
                    await event.reply("❌ I couldn't find that sticker pack. It might be private, invalid, or empty. Please try another one!")
                    return

            except Exception as e:
                    logger.error(f"Error fetching set name for user {user.id}: {e}")

            if is_premium:
                # For premium users we use special customizayion flow
                await self._start_customization_flow(event, sticker_set)
            else:
                # For regular users check cache and queue directly
                if self.cache_enabled and await self.check_cache(event, sticker_set): # cache hit
                    return
                # cache miss we got to queue it 
                await self._queue_sticker_pack(event, sticker_set, is_premium=False)

        finally:
            # remove user from adding queue set
            self._users_adding_to_queue.discard(user.id)



    async def _run_conversion(self, item, is_silent_mode: bool = False):
        """
        The core logic for converting a single sticker pack.
        Raises an Exception on any failure to signal the caller.
        """
        if not is_silent_mode:
            try:
                await self.client.edit_message(
                    entity=item.event.chat_id,
                    message=item.bot_reply_message_id,
                    text=f"⌛ Your request for the pack is now processing...",
                    buttons=None
                )
            except Exception as e:
                logger.warning(f"Could not edit message {item.bot_reply_message_id} to remove cancel button: {e}")

            status_message = await self.client.send_message(
                item.event.chat_id,
                "🚀 Starting conversion for your pack...\n"
                "🤔 Estimated time: `Calculating...`"
            )
        # sticker info
        sticker_set = item.sticker_set
        pack_title = sticker_set.set.title
        safe_pack_title = html.escape(pack_title)
        total_stickers = len(sticker_set.documents)
        num_packs = (total_stickers + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
        pack_short_name = sticker_set.set.short_name
        is_emoji_pack = sticker_set.set.emojis
        estimated_seconds = item.estimated_seconds
        processing_timeout = max(60, estimated_seconds * 3)
        status_for_db = "failed"

        pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
        pack_url = f"https://t.me/{pack_type_url}/{pack_short_name}"

        final_author = item.custom_author or self.bot_username
        final_title = item.custom_title # This can be None create wasticker pack handles this
        

        if not is_silent_mode:
            # Round off the time for better UI
            if estimated_seconds < 60:
                estimated_time_str = f"{round(estimated_seconds)} seconds"
            else:
                minutes = round(estimated_seconds / 60)
                estimated_time_str =  f"~{minutes} minute(s)"

            await self.client.edit_message(
                entity=item.event.chat_id,
                message=status_message.id,
                text=f"🚀 Starting conversion for your pack...\n"
                    f"🤔 Estimated time: {estimated_time_str}"
            )
            
            item_name = "emojis" if is_emoji_pack else "stickers"
            message = (f"📊 <b>Pack Details:</b>\n"
                    f"• Name: <a href=\"{pack_url}\">{safe_pack_title}</a>\n"
                    f"• Total {item_name}: {total_stickers}\n"
                    f"• This will create {num_packs} .wastickers file(s).")
            await self.client.send_message(item.event.chat_id, message, parse_mode='html', link_preview=False)

        # run the conversion with a timeout (either 60 sec or 2x the estimated time)
        conversion_start_time = time.monotonic()
        try:
            wastickers_files = await asyncio.wait_for(self.converter.create_wastickers_pack(sticker_set, final_author, custom_title=final_title), timeout=processing_timeout)
        except asyncio.TimeoutError:
            status_for_db = "failed_conversion_timeout"
            logger.error(f"Conversion timed out while creating .wasticker files for user {item.user_id}. Log ID: {item.log_id}")
            if not is_silent_mode:
                try:
                    await self.client.send_message(
                        item.event.chat_id,
                        (f"⏱️ The conversion for your pack took longer than expected and has timed out.❌\n"
                        f"Please try again later or with a different pack.\n\n"
                        f"If the problem persists, ping us at **{SUPPORT_GROUP}**")
                    )
                except Exception as e:
                    logger.warning(f"Could not send timeout message to {item.user_id}: {e}")
            user = await item.event.get_sender()
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "ConversionTimeout", "Creating .wasticker files took longer than expected.")
            return status_for_db # return failed status immidiately 
        except Exception as e:
            status_for_db = "failed_conversion_exception"
            logger.error(f"Conversion failed while creating .wasticker files for user {item.user_id}. Log ID: {item.log_id}")
            if not is_silent_mode:
                try:
                    await self.client.send_message(
                        item.event.chat_id,
                        (f"❌ The conversion for your pack has failed.\n"
                        f"Please try again later or with a different pack.\n\n"
                        f"If the problem persists, ping us at **{SUPPORT_GROUP}**")
                    )
                except Exception as e:
                    logger.warning(f"Could not send timeout message to {item.user_id}: {e}")
            user = await item.event.get_sender()
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, type(e).__name__, str(e))
            return status_for_db # return failed status immidiately 
    
        if not wastickers_files:
            status_for_db = "failed_no_wasticker_file"
            if not is_silent_mode:
                await self.client.send_message(item.event.chat_id, f"❌ Failed to convert the pack <b><a href=\"{pack_url}\">{safe_pack_title}</a></b>. If the problem persists, ping us at <b>{SUPPORT_GROUP}</b>", link_preview=False, parse_mode='html')
            # This is a failure so we raise an exception.
            user = await item.event.get_sender()
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "NoWastickerFileCreated", "The conversion returned no .wasticker file.")
            return status_for_db

        conversion_end_time = time.monotonic()

        conversion_duration = conversion_end_time - conversion_start_time


        # update the stats with duration
        new_cache_score = db.add_or_update_sticker_set_stats(
            set_id=sticker_set.set.id,
            short_name=pack_short_name,
            is_emoji=is_emoji_pack,
            pack_title=pack_title,
            sticker_count=total_stickers,
            conversion_duration=conversion_duration,
            is_system_process=is_silent_mode
        )

        # ------------UPLOAD -------------
        # If we get here conversion was successful now we upload to channel for cache or if cahing diabled upload directly
        cached_messages = []
        target_cache_channel = None
        if self.cache_enabled and not item.custom_title and not item.custom_author:
            target_cache_channel = db.get_or_create_cache_channel()
            if target_cache_channel:
                if not is_silent_mode:
                    await self.client.send_message(item.event.chat_id, f"✅ Conversion complete! Sending <b>{len(wastickers_files)}</b> file(s)...", link_preview=False, parse_mode='html')
                
                all_uploads_succeeded = True
                try:
                    cached_messages = await self.network_task.upload_files(wastickers_files, pack_url, safe_pack_title, target_cache_channel)
                    if not cached_messages or len(cached_messages) != len(wastickers_files): all_uploads_succeeded = False
                    # Now, log this to our database
                    if all_uploads_succeeded:
                        status_for_db = "completed"
                        cached_messages_id = [cached_message.id for cached_message in cached_messages]
                        db.add_to_cache(sticker_set.set.id, new_cache_score, target_cache_channel, cached_messages_id)
                        logger.info(f"Successfully cached pack {sticker_set.set.id} with message IDs: {cached_messages_id}")

                except* FileUploadTimeoutError as eg_t:
                    all_uploads_succeeded = False
                    status_for_db = "failed_upload_timeout_while_caching"
                    # collecting erros 
                    failed_uploads = []
                    first_failed_index = eg_t.exceptions[0].index
                    for exc in eg_t.exceptions:
                        logger.error(f"Upload timeout while caching for user {item.user_id}, file {exc.file_path}")
                        failed_uploads.append(exc.file_path)
                    # reporting to user
                    if not is_silent_mode:
                        if num_packs == 1:
                            await self.client.send_message(item.event.chat_id, f"❌ Timed out while uploading pack. Please try again later.")
                        else:
                            await self.client.send_message(item.event.chat_id, f"❌ Timed out while uploading pack part {first_failed_index+1}. Please try again later.")
                    #notify owner
                    user = await item.event.get_sender()
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "UploadTimeoutCaching", f"File: {', '.join(failed_uploads)}")
                except* FileUploadWrapperError as eg_w:
                    all_uploads_succeeded = False
                    status_for_db = "failed_upload_error_while_caching"
                    # collecting erros 
                    failed_uploads = []
                    first_failed_index = eg_w.exceptions[0].index
                    for exc in eg_w.exceptions:
                        # Log the original exception for full debug info
                        logger.error(f"Upload error while caching for user {item.user_id}, file {exc.file_path}", exc_info=exc.original_exception)
                        failed_uploads.append(exc.file_path)
                    # reporting to user
                    if not is_silent_mode:
                        if num_packs == 1:
                            await self.client.send_message(item.event.chat_id, f"❌ Failed to upload pack due to an error. Please use **/contact** to report it to the admins.")
                        else:
                            await self.client.send_message(item.event.chat_id, f"❌ Failed to upload pack part {first_failed_index+1} due to an error. Please use **/contact** to report it to the admins.")
                    #notify owner
                    user = await item.event.get_sender()
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "UploadErrorCaching", f"File: {", ".join(failed_uploads)}")
                finally:
                    # This ensures temporary .wastickers files are deleted if they weren't cached and moved.
                    for file_path in wastickers_files:
                        if os.path.exists(file_path):
                            logger.debug(f"Cleaning up temporary output file: {file_path}")
                            os.remove(file_path)
                    # if we coudnt uplaod all files sucessfully delete others too
                    if not all_uploads_succeeded and cached_messages:
                        cached_message_ids = [message.id for message in cached_messages]
                        custom_log_msg = "Failed to delete incompletely uploaded pack."
                        asyncio.create_task(self.delete_messages(target_cache_channel, cached_message_ids, custom_log_msg))

                # ----- now send from cache (if not a system task) --------
                if not is_silent_mode and all_uploads_succeeded:
                    try:
                        for message in cached_messages:
                            await self.client.send_message(entity=item.event.chat_id, message=message, link_preview=False)

                        await self.client.send_message(item.event.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")
                        status_for_db = "completed"
                    except UserIsBlockedError:
                        # some dumbass block the bot even before it sends files
                        status_for_db = "completed_but_blocked"
                        logger.error(f"User has blocked the bot! Failed to forward cached messages for pack {sticker_set.set.id,} to user {item.user_id}.")
                    except Exception as e:
                        logger.error(f"Failed to forward newly cached pack {sticker_set.set.id} to user {item.user_id}: {e}")
                        await self.client.send_message(item.event.chat_id, "❌ An error occurred while sending your files. Please use **/contact** to report this.")
                        status_for_db = "failed_forward"

        else: # caching is off or its a custom premium request
            if not is_silent_mode:
                await self.client.send_message(item.event.chat_id, f"✅ Conversion complete! Sending <b>{len(wastickers_files)}</b> file(s)...", link_preview=False, parse_mode='html')
                
                all_uploads_succeeded = True
                try:
                    await self.network_task.upload_files(wastickers_files, pack_url, safe_pack_title, item.event.chat_id)
                    status_for_db = "completed"
                except* UserIsBlockedError:
                    # some dumbass block the bot even before it sends files
                    status_for_db = "completed_but_blocked"
                    all_uploads_succeeded = False
                    logger.error(f"User has blocked the bot! Failed to send .wasticker files for pack {sticker_set.set.id,} to user {item.user_id}.")
                except* FileUploadTimeoutError as eg_t:
                    status_for_db = "failed_upload_timeout"
                    all_uploads_succeeded = False
                    # collecting erros 
                    failed_uploads = []
                    first_failed_index = eg_t.exceptions[0].index
                    for exc in eg_t.exceptions:
                        logger.error(f"Upload timeout for user {item.user_id}, file {exc.file_path}")
                        failed_uploads.append(exc.file_path)
                    # reporting user 
                    if num_packs == 1:
                        await self.client.send_message(item.event.chat_id, f"❌ Timed out while uploading pack. Please try again later.")
                    else:
                        await self.client.send_message(item.event.chat_id, f"❌ Timed out while uploading pack part {first_failed_index+1}. Please try again later.")
                    #notify owner
                    user = await item.event.get_sender()
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "UploadTimeout", f"File: {", ".join(failed_uploads)}")
                except* FileUploadWrapperError as eg_w:
                    status_for_db = "failed_upload_error"
                    all_uploads_succeeded = False
                    # collecting erros 
                    failed_uploads = []
                    first_failed_index = eg_w.exceptions[0].index
                    for exc in eg_w.exceptions:
                        # Log the original exception for full debug info
                        logger.error(f"Upload error for user {item.user_id}, file {exc.file_path}", exc_info=exc.original_exception)
                        failed_uploads.append(exc.file_path)
                    # reporting user
                    if num_packs == 1:
                        await self.client.send_message(item.event.chat_id, f"❌ Failed to upload pack due to an error. Please use **/contact** to report it to the admins.")
                    else:
                        await self.client.send_message(item.event.chat_id, f"❌ Failed to upload pack part {first_failed_index+1} due to an error. Please use **/contact** to report it to the admins.")
                    #notify owner
                    user = await item.event.get_sender()
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, sticker_set, "UploadError", f"File: {", ".join(failed_uploads)}")
                finally:
                    # This ensures temporary .wastickers files are deleted if they weren't cached and moved.
                    for file_path in wastickers_files:
                        if os.path.exists(file_path):
                            logger.debug(f"Cleaning up temporary output file: {file_path}")
                            os.remove(file_path)

                # If all uploads were successful
                if all_uploads_succeeded:
                    await self.client.send_message(item.event.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")
                    
        return status_for_db

    async def process_queue(self):
        """Process the conversion queue."""
        async with self.processing_lock:
            while True:
                item = await queue_manager.get_next_item()
                if not item:
                    break

                # ------ last cache check (if applicable) ------------------
                # check for cache suspecious item (for user items)
                if item.is_cache_suspicious:
                    logger.info(f"Re-checking cache for suspicious item from user {item.user_id} (Log ID: {item.log_id})")
                    try:
                        # We pass the item's log_id so we don't create a new DB entry
                        if await self.check_cache(item.event, item.sticker_set, log_id=item.log_id):
                            logger.info(f"Suspicious item was a cache hit! Skipping conversion.")
                            await self.client.edit_message(entity=item.event.chat_id, message=item.bot_reply_message_id, text=f"⚡ The pack you requested was processed instantly from the cache.")
                            await queue_manager.complete_processing(item.user_id, success=True)
                            continue # Success! Move to the next item in the queue.
                    except Exception as e:
                        logger.error(f"Error during suspicious cache check for log {item.log_id}: {e}")
                
                # Before running cache refresh or add, check if the pack got cached by another process (for system items)
                if item.is_silent_mode:
                    sticker_set = item.sticker_set
                    try:
                        cache_status, channel_id, msg_ids = db.is_pack_cached(sticker_set.set.id, sticker_set.set.title, len(sticker_set.documents), is_system_process=True)

                        if cache_status == 'hit':
                            # The DB says it's cached. Let's quickly verify the files are still there.
                            messages = await self.client.get_messages(channel_id, ids=msg_ids)
                            if messages and all(m is not None for m in messages):
                                # The cache is valid and exists. We can safely skip this redundant job.
                                logger.info(f"Skipping processing for pack '{sticker_set.set.short_name}' (Log ID: {item.log_id}) as it's already cached.")
                                
                                # We must properly close out this queue item and log it.
                                db.update_conversion_log(item.log_id, "completed_skipped_pre_cached", datetime.now(timezone.utc), 0.0)
                                await queue_manager.complete_processing(item.user_id, success=True)
                                
                                # And importantly clean up our system job trackers i mean those damn sets
                                if item.log_id in self.active_refresh_jobs:
                                    self.active_refresh_jobs.discard(item.log_id)
                                    if not self.active_refresh_jobs and not self.active_refresh_message:
                                        await self.client.send_message(OWNER_ID, "✅ **Cache refresh operation complete!**")
                                
                                if item.log_id in self.active_add_jobs:
                                    self.active_add_jobs.discard(item.log_id)
                                    if not self.active_add_jobs and not self.active_add_message:
                                        await self.client.send_message(OWNER_ID, "✅ **Add-to-cache operation complete!**")

                                continue # Success! Move to the next item in the queue.
                                
                    except Exception as e:
                        logger.warning(f"Pre-check failed for pack '{sticker_set.set.short_name}': {e}. Proceeding with conversion as a fallback.")


                start_time = datetime.now(timezone.utc)
                success = False 
                status_for_db = "None"
                try:                    
                    status_for_db = await self._run_conversion(item, item.is_silent_mode)

                except UserIsBlockedError:
                    status_for_db = "blocked_by_user"
                    logger.error(f"User has blocked the bot! Cannot proceed further, skiped.")
                    
                except Exception as e:
                    status_for_db = "failed_exception"
                    logger.error(f"An exception occurred while processing queue item for user {item.user_id}: {e}", exc_info=True)
                    user = await item.event.get_sender()
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, item.sticker_set, f"Some error that you never expeted: {type(e).__name__}", str(e))

                finally:
                    # Update the database log
                    completion_time = datetime.now(timezone.utc)
                    duration = (completion_time - start_time).total_seconds()
                    db.update_conversion_log(item.log_id, status_for_db, completion_time, duration)
                    if status_for_db.startswith("completed"):
                        success = True
                    await queue_manager.complete_processing(item.user_id, success)

                    # check if it was a system generated task ---------      
      
                    if item.log_id in self.active_refresh_jobs:
                        self.active_refresh_jobs.discard(item.log_id)
                        # If that was the last job, notify the owner
                        if not self.active_refresh_jobs:
                            logger.info("All cache refresh jobs have been completed.")
                            await self.client.send_message(OWNER_ID, "✅ **Cache refresh operation complete!**")

                    if item.log_id in self.active_add_jobs:
                        self.active_add_jobs.discard(item.log_id)
                        # If that was the last job, notify the owner
                        if not self.active_add_jobs and not self.active_add_message:
                            logger.info("All add-cache jobs have been completed.")
                            await self.client.send_message(OWNER_ID, "✅ **Add-to-cache operation complete!**")


    async def _get_user_from_event(self, event: events.NewMessage.Event, arg: Optional[str]) -> Optional[object]:
        """Helper to get user from command argument or reply."""
        if event.reply_to_msg_id and not arg:
            reply_msg = await event.get_reply_message()
            return await reply_msg.get_sender()
        elif arg:
            try:
                # Check if it's a numeric ID first
                if arg.isdigit():
                    return await self.client.get_entity(int(arg))
                else: # Assume it's a username
                    return await self.client.get_entity(arg)
            except Exception:
                await event.reply("❌ Invalid user ID or username.")
                return None
        return None

    # ------- User commands -----------

    @check_banned
    async def start_command(self, event: events.NewMessage.Event):
        """Handle /start command."""
        user = await event.get_sender()
        # Log user on /start
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        db.add_or_update_user(user.id, user.username, full_name)
        
        buttons = [
            [Button.inline("💎 Premium", b"premium"), Button.inline("❓ Help", b"help")],
            [Button.url("💬 Support Group", SUPPORT_GROUP_LINK), Button.inline("🤖 Commands", b"commands")]
        ]
        await event.reply(self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def help_command(self, event: events.NewMessage.Event):
        """Handle /help command."""
        buttons = [
            [Button.inline("🏠 Back to Start", b"start"), Button.inline("🤖 Commands", b"commands")]
        ]
        await event.reply(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def mystats_command(self, event: events.NewMessage.Event):
        """Displays the user's current status and conversion stats."""
        user = await event.get_sender()
        
        # user role
        role = "👤 Regular User"
        if db.is_owner(user.id):
            role = "👑 Owner"
        elif db.is_admin(user.id):
            role = "👮‍♂️ Admin"
        elif db.is_premium(user.id):
            role = "⭐ Premium User"
            duration_left = db.get_premium_duration_left(user.id)
            if duration_left:
                days = duration_left.days
                hours = duration_left.seconds // 3600
                minutes = (duration_left.seconds % 3600) // 60
                role += f"\n⏳ **Expires in**: {days}d {hours}h {minutes}m"
            
        # get conversion stats
        stats = db.get_user_stats(user.id)
        
        message = (
            f"📊 **Your Stats**\n\n"
            f"**Status**: {role}\n\n"
            f"**Conversions Log**:\n"
            f"  • Total Requests: `{stats['total']}`\n"
            f"  • ✅ Succeeded: `{stats['succeeded']}`\n"
            f"  • ❌ Failed: `{stats['failed']}`\n"
            f"  • 🚫 Cancelled: `{stats['cancelled']}`"
        )
        
        await event.reply(message)
        logger.info(f"User {user.id} has fetched their stats.")
        raise StopPropagation

    async def _get_premium_message_text(self, user_id: int) -> str:
        """Generates the dynamic premium status message for a user."""
        # Base message with premium benefits
        benefits_message = (
            f"<b>Premium Benefits Include:</b>\n"
            f"  • 🚀 <b>Priority Queue:</b> Your requests jump to the front of the line.\n"
            f"  • ⚙️ <b>Concurrent Conversions:</b> Convert up to {MAX_CONCURRENT_PREMIUM_REQUESTS} packs at once.\n"
            f"  • ✍️ <b>Custom Pack Details:</b> Set your own custom title and author name for your packs.\n"
            f"  • 💬 <b>Priority Support:</b> Get faster help in the support group."
        )

        if db.is_premium(user_id):
            duration_left = db.get_premium_duration_left(user_id)
            days = duration_left.days
            hours = duration_left.seconds // 3600
            
            status_message = (
                f"⭐ <b>You have an active Premium subscription!</b>\n"
                f"<i>Expires in: {days} days and {hours} hours.</i>\n\n"
            )
        
        else:
            status_message = (
                f"❌ <b>You are not currently a Premium user.</b>\n\n"
                f"Contact an admin at <b>{SUPPORT_GROUP}</b> to upgrade and unlock these great features!\n\n"
            )
        
        return status_message + benefits_message


    @check_banned
    async def premium_command(self, event: events.NewMessage.Event):
        """Displays the user's premium status and benefits."""
        user = await event.get_sender()
        
        
        message_text = await self._get_premium_message_text(user.id)
        buttons = [
            [Button.url("💬 Contact Admin", SUPPORT_GROUP_LINK)],
            [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
        ]

        await event.reply(message_text, buttons=buttons, parse_mode='html', link_preview=False)
        raise StopPropagation

    @check_banned
    async def queue_command(self, event: events.NewMessage.Event):
        """Command to check user's position."""
        user = await event.get_sender()
        position = queue_manager.get_queue_position(user.id)
        stats = queue_manager.get_queue_stats()

        if position:
            # User is in the queue
            message = QUEUE_CHECK_MESSAGE.format(
                position=position,
                total=stats["total_waiting"] + (1 if stats["currently_processing"] else 0)
            )
            buttons = [[Button.inline("🔄 Refresh", b"check_queue")]]
        else:
            # User is not in the queue
            message = f"📊 You're not in the queue. Total users waiting: {stats['total_waiting']}."
            buttons = [
                [Button.inline("🔄 Refresh", b"check_queue")],
                [Button.inline("🏠 Back to Start", b"start")]
            ]
        
        await event.reply(message, buttons=buttons)
        raise StopPropagation
    
    @check_banned
    async def commands_command(self, event: events.NewMessage.Event):
        """Handles the /commands command."""
        buttons = [
            [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
        ]
        await event.reply(COMMANDS_MESSAGE, buttons=buttons, parse_mode='html')
        raise StopPropagation
    

    def _format_suggestion_message(self, list_type: str) -> tuple[str, list]:
        """Helper to generate the message text and buttons for suggestions."""
        packs = self.daily_popular_packs if list_type == 'daily' else self.all_time_popular_packs
        
        if list_type == 'daily':
            title = "📅 <b>Top 10 Popular Packs (Daily)</b>"
            button = [Button.inline("🏆 View All-Time Top 50", b"suggest_all_time")]
        else: # all_time
            title = "🏆 <b>Top 50 Popular Packs (All-Time)</b>"
            button = [Button.inline("📅 View Daily Top 10", b"suggest_daily")]
        
        if not packs:
            message = f"{title}\n\n" \
                      "Hmm, I don't have any data for this yet.\n " \
                      "Check back tomorrow after more packs have been converted! 😊"
        else:
            pack_list = []
            for i, pack in enumerate(packs, 1):
                safe_title = html.escape(pack['pack_title'])
                pack_list.append(f"{i}. <a href='{pack['pack_url']}'>{safe_title}</a>")
            
            message = f"{title}\n\n<b>{"\n".join(pack_list)}</b>"
        
        return message, [button]

    @check_banned
    async def suggest_command(self, event: events.NewMessage.Event):
        """Handles the /suggest command."""
        message, buttons = self._format_suggestion_message('daily')
        await event.reply(message, buttons=buttons, parse_mode='html', link_preview=False)
        raise StopPropagation

    @check_banned
    async def contact_command(self, event: events.NewMessage.Event):
        """Handles the /contact command, prompting the user to send a message."""
        user = await event.get_sender()

        session = await self.session_manager.create(
            user_id=user.id,
            flow=Flow.CONTACT,
            state="awaiting_confirmation",
            ttl_seconds=3600, # Session expires in 1 hour
            single_active=True
        )

        buttons = [
            [Button.inline("✉️ Send Message", f"contact_send_{session.session_id}"), 
            Button.inline("❌ Cancel", f"contact_cancel_{session.session_id}")],
            [Button.url("💬 Support Group", SUPPORT_GROUP_LINK)]
        ]
        await event.reply(CONTACT_PROMPT_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    async def handle_admin_reply(self, event: events.NewMessage.Event) -> bool:
        """Handles an admin's reply, checking for duplicates before sending."""
        admin_id = event.sender_id
        reply_msg = await event.get_reply_message()
        
        me = await self.client.get_me()
        if not reply_msg or not reply_msg.sender_id == me.id:
            return False# not a reply to one of the bot's messages so let handle_message handle it

        # Extract Contact ID from the message
        contact_id_match = re.search(r"Contact ID:[^\d]*(\d+)", reply_msg.text)
        if not contact_id_match:
            return False# not a contact notification message again let handle_message handle this

        contact_id = int(contact_id_match.group(1))
        
        # Get or create a lock for this specific contact_id
        async with self.reply_locks_lock:
            entry = self.reply_locks.get(contact_id)
            if not entry:
                entry = {"lock": asyncio.Lock(), "last_used": datetime.now(timezone.utc)}
                self.reply_locks[contact_id] = entry

        contact_lock = entry["lock"]

        # update last_used immediately before we wait so cleanup won't remove it mid-wait
        entry["last_used"] = datetime.now(timezone.utc)

        async with contact_lock:
            # Check if this message has been replied already
            previous_replies = db.get_previous_replies(contact_id)

            if not previous_replies:
                # ------- First reply, no one has replied yet----------
                user_id_match = re.search(r"User ID:[^\d]*(\d+)", reply_msg.text)
                if not user_id_match:
                    await event.reply("❌ Couldn't find the original user's ID in the header.")
                    return False

                original_user_id = int(user_id_match.group(1))
                try:
                    # Get the reply content for logging
                    reply_content = self._get_message_content_for_db(event.message)
                    
                    # Send reply and logging shits
                    await self.client.send_message(original_user_id, CONTACT_ADMIN_REPLY_HEADER, parse_mode='html')
                    sent_msg = await self.client.send_message(original_user_id, event.message)
                    db.log_admin_reply(contact_id, admin_id, sent_msg.id, reply_content)
                    logger.info(f"Admin {admin_id} replied to user {original_user_id}")
                    await event.reply("✅ Reply sent to the user!")
                except Exception as e:
                    logger.error(f"Failed to send admin reply from {admin_id} to {original_user_id}: {e}")
                    await event.reply(f"❌ Failed to send reply: `{e}`")

            else:
                # ------------ Duplicate reply,some admin has/have already replied -------------
                admin_reply_msg_id = event.message.id
                buttons = [
                    [Button.inline("✔️ Yes, reply again", f"contact_force_reply_{contact_id}_{admin_reply_msg_id}")],
                    [Button.inline("❌ Cancel", "contact_cancel_reply")]
                ]
                
                prompt_text = f"⚠️ **This query has already been handled {len(previous_replies)} time(s).**\n\nAre you sure you want to send another reply?"

                # a special "details" button for owner only
                if db.is_owner(admin_id):
                    buttons.insert(1, [Button.inline("🔍 Show Reply Details", f"contact_details_{contact_id}_{admin_reply_msg_id}")])

                await event.reply(prompt_text, buttons=buttons)

            entry["last_used"] = datetime.now(timezone.utc)

        return True

    # ------------ Owner commands ----------------

    # action helper
    async def _propose_action(self, event, action_type: str, target_ids: list, message_to_send, text_to_send, no_forward, silent_broadcast):
        """Handles the confirmation flow for /send and /broadcast."""
        action_id = os.urandom(8).hex()

        # Store pending action details
        self.pending_actions[action_id] = {
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
        await self.client.send_message(event.chat_id, preview_header)

        # Send the actual content preview
        if text_to_send: 
            # Scenario: pure text from the command
            await self.client.send_message(event.chat_id, text_to_send, silent= silent_broadcast, link_preview=False)
        elif message_to_send: 
            if no_forward:
                # Scenario: Send a copy of the replied/media message
                await self.client.send_message(event.chat_id, message_to_send, silent= silent_broadcast)
            else:
                # Scenario: Forward the replied/media message
                await self.client.forward_messages(event.chat_id, message_to_send, silent= silent_broadcast)

        # Send confirmation prompt
        buttons = [
            [Button.inline(f"✅ Yes, {action_type.capitalize()}", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await self.client.send_message(event.chat_id, f"Do you want to proceed with this {action_type}?", buttons=buttons)
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
        user_ids = db.get_all_user_ids() 
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
                user_entity = await self.client.get_entity(entity_to_find)
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


    async def _gstats_send_list(self, event: events.CallbackQuery.Event, title: str, content: str, filename: str):
        """Helper to send gstats lists, sending as a file if too long."""

        buttons = [[Button.inline("⬅️ Back to Stats", b"gstats_back")]]
        header = f"📋 <b>{title}</b>\n\n"

        if not content.strip():
            await event.edit(header + f"The list for <code>{title}</code> is empty.", buttons= buttons, link_preview=False, parse_mode='html')
            return


        if len(header + content) > 4000: # A bit less than 4096 to be safe
            file_content = content.replace("`", "").replace("*", "") # Clean formatting for .txt
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(file_content)

            # Delete the previous message and send a new one with the file
            await event.delete()
            await self.client.send_file(
                event.chat_id,
                filename,
                caption=f"The list of **{title}** was too long, so I've sent it as a file.",
                buttons=buttons
            )
            os.remove(filename)
        else:
            await event.edit(header + content, buttons=buttons, link_preview=False, parse_mode='html')


    async def _get_gstats_message_and_buttons(self) -> tuple[str, list]:
        """Helper to generate the main /gstats message and buttons."""
        stats = db.get_gstats()
        q_stats = queue_manager.get_queue_stats()

        processing_user = q_stats['processing_user'] or "None"

        message = (
            f"📊 **Global Bot Statistics**\n\n"
            f"👤 **Users:**\n"
            f"  • Total Users: `{stats['total_users']}`\n"
            f"  • Admins: `{stats['total_admins']}`\n"
            f"  • Active Premium: `{stats['active_premium']}`\n"
            f"  • Banned Users: `{stats['total_banned']}`\n\n"
            f"⚙️ **Conversions (Overall):**\n"
            f"  • ✅ Succeeded: `{stats['total_succeeded']}`\n"
            f"  • ❌ Failed: `{stats['total_failed']}`\n"
            f"  • 🚫 Cancelled: `{stats['total_cancelled']}`\n\n"
            f"📈 **Conversions (Today):**\n"
            f"  • ✅ Succeeded: `{stats['today_succeeded']}`\n"
            f"  • ❌ Failed: `{stats['today_failed']}`\n"
            f"  • 🚫 Cancelled: `{stats['today_cancelled']}`\n\n"
            f"⏳ **Live Queue Status:**\n"
            f"  • Waiting: `{q_stats['total_waiting']}`\n"
            f"  • Currently Processing: `{processing_user}`"
        )

        buttons = [
            [Button.inline("⭐ Premium Members", b"gstats_premium"), Button.inline("🏆 Top 50 Users", b"gstats_top_users")],
            [Button.inline("👮‍♂️ Admins List", b"gstats_admins"), Button.inline("🚫 Banned List", b"gstats_banned")],
            [Button.inline("🔄 Refresh", b"gstats_refresh")]
        ]
        return message, buttons

    async def gstats_command(self, event: events.NewMessage.Event):
        """Owner command to view global bot statistics."""
        message, buttons = await self._get_gstats_message_and_buttons()
        await event.reply(message, buttons=buttons)
        raise StopPropagation

    # owner's command
    async def promote_command(self, event: events.NewMessage.Event):
        """Owner command to promote a user to admin."""
        if not db.is_owner(event.sender_id):
            return # Silently ignore for non-owners
        
        try:
            target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
            if not target_user:
                await event.reply("ℹ️ Usage: `/promote <user_id/@username>` or reply to a user's message.")
                return

            if db.is_admin(target_user.id):
                await event.reply(f"️🤷‍♂️ User `{target_user.id}` is already an admin.")
                return
                
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            db.add_admin(target_user.id, target_user.username, event.sender_id)
            await event.reply(f"👑 Successfully promoted **{full_name}** (`{target_user.id}`) to Admin!")
            logger.info(f"User {target_user.id} promoted to admin by {event.sender_id}")
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
            target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
            if not target_user:
                await event.reply("ℹ️ Usage: `/demote <user_id/@username>` or reply to a user's message.")
                return

            if not db.is_admin(target_user.id) or db.is_owner(target_user.id):
                await event.reply(f"🤷‍♂️ User `{target_user.id}` is not a promotable/demotable admin.")
                return

            if db.remove_admin(target_user.id, event.sender_id):
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
        """Owner command to get the database file."""    
        db_path = os.path.realpath(os.path.expanduser(DB_PATH))    

        if os.path.exists(db_path):
            logger.info(f"Owner {event.sender_id} requested the database file.")
            try:
                await asyncio.wait_for(event.reply("📦 Here is the database file.", file=db_path), DB_UPLOAD_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"Database file upload timed out.")
                await event.reply("❌ Error: Database file upload timed out.")
        else:
            logger.error(f"Owner {event.sender_id} requested DB, but it was not found at {db_path}.")
            await event.reply("❌ Error: `bot_data.db` not found in the specified directory.")
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
                    await asyncio.wait_for(self.client.send_file(event.chat_id, zip_path, caption=f"Here are all {len(all_logs)} log files."), UPLOAD_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error(f"Logs file upload timed out.")
                    await event.reply("Error: Logs file upload timed out.")
                    return
                except Exception as e:
                    logger.error(f"Logs file upload failed. Error: {e}")
                    await event.reply("Error: Logs file upload failed.\n**Error**: {e}")
                    return
            except Exception as e:
                logger.error(f"An error occured: {e}")
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path) # Clean up the zip file

        else: # Send the latest (current) log
            logger.info(f"Owner {event.sender_id} requested the latest log file.")
            # Find the uncompressed .log file (logrotate leaves today's log uncompressed)
            # .screenrc names it based on session and window e.g. tgBot-0.log
            try:
                latest_logs = glob.glob(os.path.join(log_dir, '*.log'))
                
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
                        await event.reply("Error: Logs file upload failed.\n**Error**: {e}")
                        return
                else:
                    logger.error(f"Warning: No .log files found in {log_dir}")
                    await event.reply("🤔 No `.log` file found. Seems something's wrong.")
            except Exception as e:
                logger.error(f"An error occured while getting logs: {e}")
        raise StopPropagation
    
    async def toggle_cache_command(self, event: events.NewMessage.Event):
        """Owner command to enable or disable the caching system."""

        arg = event.text.split(maxsplit=1)

        if len(arg) > 1 and arg[1].lower() == 'on':
            self.cache_enabled = True
            await event.reply("✅ Caching system has been **enabled**.")
        elif len(arg) > 1 and arg[1].lower() == 'off':
            self.cache_enabled = False
            await event.reply("❌ Caching system has been **disabled**.")
        else:
            status = "ENABLED" if self.cache_enabled else "DISABLED"
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
            all_packs = db.get_all_cached_packs()
            if not all_packs:
                await event.reply("✅ The cache is already empty. Nothing to do!")
                return
            
            action_type = "clearcache_all"
            confirm_message = f"🗑️ Are you sure you want to clear the **entire cache**? This will remove **{len(all_packs)}** packs and cannot be undone."
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
            
        self.pending_actions[action_id] = {
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
        if self.active_refresh_jobs:
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

        self.pending_actions[action_id] = { "action_type": action_type, "payload": action_payload, "original_event": event }
        buttons = [
            [Button.inline("✅ Yes, Refresh", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await event.reply(confirm_message, buttons=buttons)
        raise StopPropagation

    async def cancelrefresh_command(self, event: events.NewMessage.Event):
        """Owner command to cancel an ongoing cache refresh operation."""
        if not self.active_refresh_jobs:
            await event.reply("✅ No active cache refresh operation to cancel.")
            return

        msg = await event.reply(f"Cancelling {len(self.active_refresh_jobs)} queued refresh jobs...")
        
        cancelled_count = 0
        # Create a copy to iterate over, as the set will be modified
        jobs_to_cancel = list(self.active_refresh_jobs)
        for log_id in jobs_to_cancel:
            # The OWNER_ID is used as the user_id for system tasks
            if await queue_manager.cancel_item(user_id=SYSTEM_USER_ID, log_id=log_id):
                db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
                cancelled_count += 1

        self.active_refresh_jobs.clear()
        
        if self.active_refresh_message:
            try:
                await self.client.edit_message(self.active_refresh_message.chat_id, self.active_refresh_message.id, "❌ Cache refresh operation cancelled by user.")
                self.active_refresh_message = None
            except Exception:
                pass # Message might have been deleted

        await msg.edit(f"✅ Cancelled **{cancelled_count}** pending jobs from the queue.")
        raise StopPropagation

    async def _execute_refresh_task(self, action_type: str, payload: dict, original_event: events.NewMessage.Event):
        """The background task that fetches pack details and queues them for refresh."""
        system_id = SYSTEM_USER_ID
        packs_to_queue = []
        
        if action_type == "refreshcache_top_n":
            limit = payload['limit']
            if self.active_refresh_message: await self.client.edit_message(self.active_refresh_message, f"Step 1/2: Clearing entire cache...")
            
            # Clear entire cache
            all_packs = db.get_all_cached_packs()
            asyncio.create_task(self.delete_multiple_cache(all_packs))

            all_packs_short_name = None
            if limit == "all":
                all_packs_short_name = db.get_all_packs()
            packs_to_queue = all_packs_short_name if limit == "all" else db.get_top_packs_by_score(limit) 

        elif action_type == "refreshcache_links":
            pack_names = payload['pack_short_names']
            if self.active_refresh_message: await self.client.edit_message(self.active_refresh_message, f"Step 1/2: Clearing cache for {len(pack_names)} specified packs...")
            # Clear specified packs and prepare for queueing
            set_ids = []
            for name in pack_names:
                set_id = db.get_set_id_by_short_name(name)
                if set_id:
                    set_ids.append(set_id)
            asyncio.create_task(self.delete_multiple_cache(set_ids))

            packs_to_queue = pack_names

        total_to_queue = len(packs_to_queue)
        if self.active_refresh_message: await self.client.edit_message(self.active_refresh_message, f"Step 2/2: Checking and Queueing {total_to_queue} packs for conversion...")
        else: return

        queued_count = 0
        for short_name in packs_to_queue:

            if queued_count > 0 and not self.active_refresh_jobs:
                logger.info("Refresh operation was cancelled. Halting queueing task.")
                break

            try:
                sticker_set = await self.network_task.get_sticker_set(short_name)
                if not sticker_set or not sticker_set.documents: continue

                estimated_seconds = estimate_wait_time(sticker_set.documents, None)
                is_emoji = sticker_set.set.emojis
                pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{short_name}"
                log_id = db.log_conversion_request(system_id, sticker_set.set.id, pack_url, is_emoji)
                
                await queue_manager.add_to_queue(
                    user_id=system_id, username="System Refresh", bot_reply_message_id=original_event.id,
                    sticker_set=sticker_set, estimated_seconds=estimated_seconds, log_id=log_id,
                    priority=SYSTEM_PRIORITY, event=original_event, is_cache_suspicious=False,
                    is_silent_mode=True
                )
                self.active_refresh_jobs.add(log_id)
                queued_count += 1
                if queued_count % 10 == 0 and self.active_refresh_message: # Update every 10 packs
                    await self.client.edit_message(self.active_refresh_message, f"Step 2/2: Queued {queued_count}/{total_to_queue} packs...")

            except Exception as e:
                logger.error(f"Failed to queue pack {short_name} for refresh: {e}")

        
        if self.active_refresh_message: 
            await self.client.edit_message(self.active_refresh_message, f"✅ Successfully queued **{queued_count}/{total_to_queue}** packs for cache refresh.\nConversions will now run in the background with low priority.")
        self.active_refresh_message = None # We're done editing this message
        # start the queue if its not running
        if not self.processing_lock.locked():
            is_processing = queue_manager.get_queue_stats()["currently_processing"]
            if not is_processing:
                asyncio.create_task(self.process_queue())

    
    async def addcache_command(self, event: events.NewMessage.Event):
        """Owner command to add non-cached packs to the cache."""
        if self.active_add_jobs:
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

        self.pending_actions[action_id] = {"action_type": action_type, "payload": action_payload, "original_event": event}
        buttons = [
            [Button.inline("✅ Yes, Proceed", data=f"confirm_action_{action_id}")],
            [Button.inline("❌ Cancel", data=f"cancel_action_{action_id}")]
        ]
        await event.reply(confirm_message, buttons=buttons)
        raise StopPropagation


    async def canceladdcache_command(self, event: events.NewMessage.Event):
        """Owner command to cancel an ongoing add-cache operation."""
        active_session = await self.session_manager.get_active_latest(event.sender_id, Flow.ADDCACHE)

        if not self.active_add_jobs and not active_session:
            await event.reply("✅ No active add-cache operation to cancel.")
            return
        
        # Handle cancelling the interactive mode
        if active_session:
            await self.session_manager.expire(event.sender_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ Interactive add-cache mode has been cancelled.")

        if not self.active_add_jobs:
            return # No background jobs to cancel

        msg = await event.reply(f"Cancelling {len(self.active_add_jobs)} queued add-cache jobs...")

        cancelled_count = 0
        jobs_to_cancel = list(self.active_add_jobs)
        for log_id in jobs_to_cancel:
            if await queue_manager.cancel_item(user_id=SYSTEM_USER_ID, log_id=log_id):
                db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
                cancelled_count += 1

        self.active_add_jobs.clear()

        if self.active_add_message:
            try:
                await self.client.edit_message(self.active_add_message.chat_id, self.active_add_message.id, "❌ Add-cache operation cancelled by user.")
                self.active_add_message = None
            except Exception:
                pass

        await msg.edit(f"✅ Cancelled **{cancelled_count}** pending jobs from the queue.")
        raise StopPropagation

    async def done_command(self, event: events.NewMessage.Event):
        """Owner command to exit interactive add-cache mode."""
        active_session = await self.session_manager.get_active_latest(event.sender_id, Flow.ADDCACHE)
        if active_session:
            await self.session_manager.expire(event.sender_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ **Finished!** Exited interactive add-cache mode.")
        else:
            await event.reply("✅ You are not in an active interactive mode.")
        # Silently ignore if not in the correct state
        raise StopPropagation

    async def _execute_addcache_task(self, action_type: str, payload: dict, original_event: events.NewMessage.Event):
        """The background task that fetches, verifies, and queues non-cached packs."""
        system_id = SYSTEM_USER_ID
        packs_to_process = []

        # Step 1: Get the list of pack short_names to process
        try:
            if action_type == "addcache_links":
                packs_to_process = payload['pack_short_names']
                if self.active_add_message: await self.client.edit_message(self.active_add_message, f"Step 1/2: Preparing to check {len(packs_to_process)} specified packs...")

            elif action_type == "addcache_all":
                if self.active_add_message: await self.client.edit_message(self.active_add_message, "Step 1/2: Fetching ALL non-cached packs from the database...")
                packs_to_process = await asyncio.to_thread(db.get_non_cached_packs)

            elif action_type == "addcache_n":
                limit = payload['limit']
                if self.active_add_message: await self.client.edit_message(self.active_add_message, f"Step 1/2: Fetching top {limit} non-cached packs from the database...")
                packs_to_process = await asyncio.to_thread(db.get_non_cached_packs, limit=limit)
        except Exception as e:
            logger.error(f"AddCache: Failed to fetch packs from DB: {e}", exc_info=True)
            if self.active_add_message: await self.client.edit_message(self.active_add_message, f"❌ Failed to fetch pack list from database: {e}")
            return

        total_to_process = len(packs_to_process)
        if self.active_add_message: await self.client.edit_message(self.active_add_message, f"Step 2/2: Checking and queueing {total_to_process} packs...")
        else: return

        queued_count = 0
        skipped_count = 0
        failed_count = 0

        for i, short_name in enumerate(packs_to_process, 1):
            if i > 1 and not self.active_add_message:
                logger.info("Add-cache operation was cancelled. Halting queueing task.")
                break
            
            try:
                sticker_set = await self.network_task.get_sticker_set(short_name)
                if not sticker_set or not sticker_set.documents:
                    logger.warning(f"AddCache: Could not fetch sticker set for '{short_name}'. Skipping.")
                    failed_count += 1
                    continue
                
                set_id = sticker_set.set.id
                set_title = sticker_set.set.title
                set_count = len(sticker_set.documents)

                cache_status, channel_id, message_ids = db.is_pack_cached(set_id, set_title, set_count, is_system_process=True)
                
                if cache_status == 'hit':
                    try:
                        messages = await self.client.get_messages(channel_id, ids=message_ids)
                        if messages and all(msg is not None for msg in messages):
                            skipped_count += 1
                            continue
                        else:
                            logger.warning(f"AddCache: Inconsistent cache for {short_name}. Clearing and re-queueing.")
                            asyncio.create_task(self.delete_cache(set_id))
                    except Exception as e:
                        logger.error(f"AddCache: Error verifying messages for {short_name}: {e}. Re-queueing.")
                        asyncio.create_task(self.delete_cache(set_id))
                
                elif cache_status == 'stale':
                    logger.warning(f"AddCache: Stale cache for {short_name}. Clearing and re-queueing.")
                    asyncio.create_task(self.delete_cache(set_id))

                estimated_seconds = estimate_wait_time(sticker_set.documents, None)
                is_emoji = sticker_set.set.emojis
                pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{short_name}"
                log_id = db.log_conversion_request(system_id, sticker_set.set.id, pack_url, is_emoji)
                
                await queue_manager.add_to_queue(
                    user_id=system_id, username="System AddCache", bot_reply_message_id=original_event.id,
                    sticker_set=sticker_set, estimated_seconds=estimated_seconds, log_id=log_id,
                    priority=SYSTEM_PRIORITY, event=original_event, is_cache_suspicious=False,
                    is_silent_mode=True
                )
                self.active_add_jobs.add(log_id)
                queued_count += 1

                if i % 10 == 0 and self.active_add_message:
                    await self.client.edit_message(self.active_add_message, f"Step 2/2: Progress...\n- Queued: {queued_count}\n- Skipped: {skipped_count}\n- Failed: {failed_count}\n- Total: {i}/{total_to_process}")

            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to queue pack {short_name} for add-cache: {e}")

        if self.active_add_message:
            final_message = (
                f"✅ **Add-Cache Queuing Complete!**\n\n"
                f"• Successfully queued: **{queued_count}**\n"
                f"• Skipped (already cached): **{skipped_count}**\n"
                f"• Failed to queue: **{failed_count}**\n\n"
                f"Conversions will now run in the background with low priority."
            )
            await self.client.edit_message(self.active_add_message, final_message)

        self.active_add_message = None
        if not self.processing_lock.locked():
            is_processing = queue_manager.get_queue_stats()["currently_processing"]
            if not is_processing:
                asyncio.create_task(self.process_queue())

    # ------------- Admins commands ---------------

    async def add_premium_command(self, event: events.NewMessage.Event):
        """Admin command to add a premium user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation # Silently ignore for non-admins

        user_arg = event.pattern_match.group(1)
        duration_arg = event.pattern_match.group(2)
        
        # If both are None, it means the command was likely just /addpremium
        if not user_arg and not duration_arg:
            await event.reply("ℹ️ **Usage:** `/addpremium <user_id/@username> <days>`\nOr, reply to a user's message with `/addpremium <days>`.")
            raise StopPropagation

        # Logic to handle different argument combinations
        target_user = None
        duration_days = None

        if user_arg and user_arg.isdigit() and not duration_arg:
            # Case: /addpremium <days> (with reply)
            duration_days = int(user_arg)
            target_user = await self._get_user_from_event(event, None) # Get from reply
        elif user_arg and duration_arg:
            # Case: /addpremium <user> <days>
            duration_days = int(duration_arg)
            target_user = await self._get_user_from_event(event, user_arg)
        else:
            await event.reply("ℹ️ **Invalid format.**\nUsage: `/addpremium <user_id/@username> <days>`\nOr, reply to a user's message with `/addpremium <days>`.")
            raise StopPropagation

        if not target_user:
            await event.reply("❌ **User not found.** You must specify a user by their ID/@username or by replying to their message.")
            raise StopPropagation
        
        if not duration_days or duration_days <= 0:
            await event.reply("❌ **Invalid duration.** Please provide a positive number of days.")
            raise StopPropagation
        
        if db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user is already premium. Use `/extendpremium` to extend their duration.")
            raise StopPropagation
            
        try:
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            db.add_premium(target_user.id, target_user.username, duration_days, event.sender_id)
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except Exception as e:
            logger.error(f"An error has occurred while adding {target_user.id} to premium by {event.sender_id}. Error: {e}")
            await event.reply("❌ An error has occurred maybe this is not a valid user or the user hasn't started the bot.")
            raise StopPropagation
        
        expiry = datetime.now(timezone.utc) + timedelta(days=duration_days)
        
        await event.reply(
            f"⭐ Successfully granted premium to **{full_name}** (`{target_user.id}`)!\n"
            f"Expires in: `{duration_days}` days (on `{expiry.strftime('%Y-%m-%d %H:%M')} UTC`)."
        )
        logger.info(f"User {target_user.id} granted {duration_days} days of premium by admin: {event.sender_id}")
        raise StopPropagation
    
    async def remove_premium_command(self, event: events.NewMessage.Event):
        """Admin command to remove a premium user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation

        target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/removepremium <user_id/@username>` or reply to a user.")
            raise StopPropagation
        
        if not db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user does not have an active premium subscription.")
            raise StopPropagation

        if db.remove_premium(target_user.id, event.sender_id):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"✅ Premium status for **{full_name}** (`{target_user.id}`) has been revoked.")
            logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
        else:
            await event.reply("❌ An error occurred. Could not remove premium status.")
        raise StopPropagation

    async def extend_premium_command(self, event: events.NewMessage.Event):
        """Admin command to extend a premium user's subscription."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation
        
        user_arg = event.pattern_match.group(1)
        days_arg = event.pattern_match.group(2)

        if not user_arg or not days_arg:
             await event.reply("ℹ️ **Usage:** `/extendpremium <user_id/@username> <days>`.")
             raise StopPropagation
        
        target_user = await self._get_user_from_event(event, user_arg)
        if not target_user:
            await event.reply("❌ User not found.")
            raise StopPropagation

        if not db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user isn't premium. Use `/addpremium` to grant them premium first.")
            raise StopPropagation
        
        days_to_add = int(days_arg)
        try:
            new_expiry = db.manage_premium_duration(target_user.id, days_to_add, event.sender_id, 'extended')
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except Exception as e:
            await event.reply("❌ An unknown error has occurred; please contact the developer")
            raise StopPropagation

        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        await event.reply(
            f"✅ Extended premium for **{full_name}** by `{days_to_add}` days.\n"
            f"New expiry date: `{new_expiry.strftime('%Y-%m-%d %H:%M')}`."
        )
        logger.info(f"Premium of user {target_user.id} has been extended by {days_to_add} days by admin: {event.sender_id}")
        raise StopPropagation

    async def deduct_premium_command(self, event: events.NewMessage.Event):
        """Admin command to deduct days from a premium user's subscription."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation

        user_arg = event.pattern_match.group(1)
        days_arg = event.pattern_match.group(2)
        
        if not user_arg or not days_arg:
             await event.reply("ℹ️ **Usage:** `/deductpremium <user_id/@username> <days>`.")
             raise StopPropagation
        
        target_user = await self._get_user_from_event(event, user_arg)
        if not target_user:
            await event.reply("❌ User not found.")
            raise StopPropagation

        if not db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user does not have an active premium subscription.")
            raise StopPropagation
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        current_days_left= db.get_premium_duration_left(target_user.id).days
        if int(days_arg) > current_days_left:
            if db.remove_premium(target_user.id, event.sender_id):
                await event.reply(f"✅ Since **{full_name}** had only `{current_days_left}` days of premium left, they have been **removed** from premium.")
                logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
            else:
                await event.reply("❌ An error occurred. Could not remove premium status.")
            raise StopPropagation

        days_to_deduct = -abs(int(days_arg)) # Ensure it's a negative number
        try:
            new_expiry = db.manage_premium_duration(target_user.id, days_to_deduct, event.sender_id, 'deducted')
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except Exception as e:
            await event.reply("❌ An error occurred. Could not deduct premium.")
            raise StopPropagation

        
        expiry_message = f"New expiry date: `{new_expiry.strftime('%Y-%m-%d %H:%M')}`."

        await event.reply(
            f"✅ Deducted `{abs(days_to_deduct)}` days from **{full_name}**'s premium.\n{expiry_message}"
        )
        logger.info(f"Premium of user {target_user.id} has been deducted by {abs(days_to_deduct)} days by admin: {event.sender_id}")
        raise StopPropagation

    async def getstats_command(self, event: events.NewMessage.Event):
        """Admin command to get conversion stats for a specific user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation # Only admins can use this

        target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/getstats <user_id/@username>` or reply to a user's message.")
            raise StopPropagation
        
        # Get user role for display
        role = "👤 Regular User"
        if db.is_owner(target_user.id):
            role = "👑 Owner"
        elif db.is_admin(target_user.id):
            role = "👮‍♂️ Admin"
        elif db.is_premium(target_user.id):
            role = "⭐ Premium User"
            duration_left = db.get_premium_duration_left(target_user.id)
            if duration_left:
                days = duration_left.days
                hours = duration_left.seconds // 3600
                minutes = (duration_left.seconds % 3600) // 60
                role += f"\n⏳ **Expires in**: {days}d {hours}h {minutes}m"
        
        stats = db.get_user_stats(target_user.id)
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        
        message = (
            f"📊 **Stats for {full_name}** (`{target_user.id}`)\n\n"
            f"**Status**: {role}\n\n"
            f"**Conversions Log**:\n"
            f"  • Total Requests: `{stats['total']}`\n"
            f"  • ✅ Succeeded: `{stats['succeeded']}`\n"
            f"  • ❌ Failed: `{stats['failed']}`\n"
            f"  • 🚫 Cancelled: `{stats['cancelled']}`"
        )
        logger.info(f"Stats of user {target_user.id} has been fetched by admin: {event.sender_id}")
        await event.reply(message)
        raise StopPropagation
    
    async def _parse_user_and_reason(self, event: events.NewMessage.Event) -> tuple[Optional[object], str]:
        """
        Parses a command event to extract the target user and the reason.
        Handles both replies and direct user arguments.
        """
        target_user = None
        reason = "No reason provided."
        command_text = event.text or ""

        # if its a reply 
        if event.reply_to_msg_id:
            target_user = await self._get_user_from_event(event, None)
            if target_user:
                parts = command_text.split(maxsplit=1)
                if len(parts) > 1:
                    reason = parts[1]
        else:
            # Not a reply, parse user and reason from the command text
            parts = command_text.split(maxsplit=2)
            user_arg = parts[1] if len(parts) > 1 else None
            reason = parts[2] if len(parts) > 2 else reason
            target_user = await self._get_user_from_event(event, user_arg)
        
        return target_user, reason

    # silent ban command
    async def sban_command(self, event: events.NewMessage.Event):
        """Admin command to SILENTLY ban a user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation
        
        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/sban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation

        db.ban_user(target_user.id, event.sender_id, reason, is_silent=True)
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        await event.reply(f"🚫 **Silently Banned {full_name}** (`{target_user.id}`).")
        logger.info(f"User {target_user.id} silently banned by admin: {event.sender_id}. Reason: {reason}")
        raise StopPropagation
    
    # notified ban command 
    async def ban_command(self, event: events.NewMessage.Event):
        """Admin command to ban a user and NOTIFY them."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/ban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation
        
        if db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is already banned.")
            raise StopPropagation

        db.ban_user(target_user.id, event.sender_id, reason, is_silent=False)
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        logger.info(f"User {target_user.id} banned by admin: {event.sender_id}. Reason: {reason}")
        
        notification_status = ""
        try:
            await self.client.send_message(
                target_user.id,
                f"You have been banned from using this bot by an administrator.\n\n**Reason:** {reason}"
            )
            notification_status = "User has been notified."
        except Exception as e:
            logger.warning(f"Could not notify user {target_user.id} about their ban: {e}")
            notification_status = "Could not notify the user (they may have blocked the bot or haven't started yet)."

        await event.reply(f"🚫 **Banned {full_name}** (`{target_user.id}`).\n{notification_status}")
        raise StopPropagation

    # unban command
    async def unban_command(self, event: events.NewMessage.Event):
        """Admin command to unban a user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)
            
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/unban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if not db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is not currently banned.")
            raise StopPropagation

        if db.unban_user(target_user.id, event.sender_id, reason):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"✅ **Unbanned {full_name}** (`{target_user.id}`). They can now use the bot again.")
            logger.info(f"User {target_user.id} unbanned by admin: {event.sender_id}. Reason: {reason}")
        else:
            await event.reply("❌ An error occurred. User might have already been unbanned.")
        raise StopPropagation

    @check_banned
    async def handle_callback_query(self, event: events.CallbackQuery.Event):
        """Handle callback queries from inline keyboards."""
        user_id = event.sender_id

        # Get or create a lock for this user
        async with self.user_callback_locks_lock:
            if user_id not in self.user_callback_locks:
                self.user_callback_locks[user_id] = {"lock": asyncio.Lock(), "last_used": datetime.now(timezone.utc)}
            
            user_lock_entry = self.user_callback_locks[user_id]
            user_lock = user_lock_entry["lock"]
            user_lock_entry["last_used"] = datetime.now(timezone.utc) # Update last used

        if user_lock.locked():
            # if its already locked, its a rapid click
            await event.answer("Hey, please click one at a time 😓")
            return 
        
        async with user_lock:
            data = event.data.decode('utf-8')

            if data == "check_membership":
                await event.answer()
                if await self.check_user_membership(user_id):
                    buttons = [
                        [Button.inline("💎 Premium", b"premium"), Button.inline("❓ Help", b"help")],
                        [Button.url("💬 Support Group", SUPPORT_GROUP_LINK), Button.inline("🤖 Commands", b"commands")]
                    ]
                    await event.edit("✅ Great! You're now a member.\n\n" + self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
                else:
                    try:
                        await event.edit("❌ You still need to join the required channels.\n\n" + CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
                    except Exception as e:
                        logger.warning(f"Could not edit the Join message: {e}")
            
            elif data.startswith("cancel_item_"):
                await event.answer()
                log_id = int(data.split("_", 2)[2])
                success = await queue_manager.cancel_item(user_id, log_id)
                if success:
                    db.update_conversion_log(log_id, "cancelled", datetime.now(timezone.utc), 0.0)
                    await event.edit("✅ Your request has been successfully cancelled.")
                else:
                    await event.edit("❌ Could not cancel. The item may be processing or completed.")

            elif data == "check_queue":
                await event.answer()
                position = queue_manager.get_queue_position(user_id)
                stats = queue_manager.get_queue_stats()
                if position:
                    message = QUEUE_CHECK_MESSAGE.format(
                        position=position,
                        total=stats["total_waiting"] + (1 if stats["currently_processing"] else 0)
                    )
                    buttons = [[Button.inline("🔄 Refresh", b"check_queue")]]
                else:
                    message = f"📊 You're not in the queue. Total users waiting: {stats['total_waiting']}."
                    buttons = [
                        [Button.inline("🔄 Refresh", b"check_queue")],
                        [Button.inline("🏠 Back to Start", b"start")]
                    ]
                try:
                    await event.edit(message, buttons=buttons)
                except Exception as e:
                    logger.debug(f"Could not edit the check_queue message: {e}")
            
            elif data == "help":
                await event.answer()
                buttons = [
                    [Button.inline("🏠 Back to Start", b"start"), Button.inline("🤖 Commands", b"commands")]
                ]
                await event.edit(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

            elif data == "start":
                await event.answer()
                buttons = [
                    [Button.inline("💎 Premium", b"premium"), Button.inline("❓ Help", b"help")],
                    [Button.url("💬 Support Group", SUPPORT_GROUP_LINK), Button.inline("🤖 Commands", b"commands")]
                ]
                await event.edit(self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
            
            elif data == "premium":
                await event.answer()
                message_text = await self._get_premium_message_text(user_id)
                buttons = [
                    [Button.url("💬 Contact Admin", SUPPORT_GROUP_LINK)],
                    [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
                ]
                await event.edit(message_text, buttons=buttons, parse_mode='html', link_preview=False)

            elif data == "commands":
                await event.answer()
                buttons = [
                    [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
                ]
                await event.edit(COMMANDS_MESSAGE, buttons=buttons, parse_mode='html')

            elif data.startswith("contact_send_"):
                await event.answer()
                *_, sid = data.split("_", 2)
                session = await self.session_manager.get(user_id, Flow.CONTACT, sid)

                if session and session.active:
                    # Update the session state to wait for the user's message
                    await self.session_manager.update(user_id, Flow.CONTACT, sid, state="awaiting_contact_message", ttl_seconds=3600)
                    prompt_message = await event.edit(
                        "✅ Great! Please send the message you'd like to forward now. You can reply to this message or send a new one.", 
                        buttons=[Button.inline("❌ Cancel", f"contact_cancel_{sid}")]
                    )
                    # Link this message to the session so we can identify replies
                    await self.session_manager.mark_message(user_id, Flow.CONTACT, sid, event.chat_id, prompt_message.id)
                else:
                    await event.edit("This action has expired. Please use /contact again.")

            elif data.startswith("contact_cancel_"):
                await event.answer()
                *_, sid = data.split("_", 2)
                session = await self.session_manager.get(user_id, Flow.CONTACT, sid)

                if session and session.active:
                    # Expire the session to deactivate it
                    await self.session_manager.expire(user_id, Flow.CONTACT, sid)
                    await event.edit("Action cancelled.", buttons=None)
                else:
                    await event.edit("This action has expired or already completed.")

            elif data.startswith("contact_force_reply_"):
                await event.answer()
                try:
                    *_, contact_id_str, admin_msg_id_str = data.split("_")
                    contact_id = int(contact_id_str)
                    admin_msg_id = int(admin_msg_id_str)

                    # Fetch details of the original user
                    details = db.get_contact_details(contact_id)
                    if not details:
                        logger.error(f"Failed to get the original contact message of contact ID {contact_id}.")
                        await event.edit("❌ Error: Could not find the original contact message.")
                        return

                    original_user_id = details['user_message']['user_id']
                    
                    # Fetch the admin's reply message
                    admin_msg = await self.client.get_messages(event.chat_id, ids=admin_msg_id)
                    reply_content = self._get_message_content_for_db(admin_msg)
                    
                    # Send the reply and log it
                    await self.client.send_message(original_user_id, CONTACT_ADMIN_REPLY_HEADER, parse_mode='html')
                    sent_msg = await self.client.send_message(original_user_id, admin_msg)
                    db.log_admin_reply(contact_id, user_id, sent_msg.id, reply_content)
                    logger.info(f"An admin replied to the already replied user {original_user_id}")
                    await event.edit("✅ Your additional reply has been sent.")
                except Exception as e:
                    logger.error(f"Failed to send duplicate admin reply to {original_user_id}: {e}")
                    await event.edit(f"❌ An error occurred: {e}")

            elif data.startswith("contact_details_"):
                await event.answer()
                *_, contact_id_str, admin_msg_id_str = data.split("_")
                contact_id = int(contact_id_str)
                
                details = db.get_contact_details(contact_id)
                if not details:
                    await event.edit("❌ Could not retrieve details for this contact.")
                    return

                # Format the details into a nice, readable message
                user_msg = details['user_message']
                admin_reps = details['admin_replies']
                user_message_text = user_msg['user_message_text'] if len(user_msg['user_message_text']) <= 1000 else user_msg['user_message_text'][:994] + "......"
                sent_time = user_msg['action_time_sent'].strftime('%Y-%m-%d %H:%M:%S')
                safe_user_name = html.escape(user_msg['user_full_name'])
                safe_user_message = html.escape(user_message_text)
                response_text = (
                    f"📖 <b>Contact Details for Ticket #{contact_id}</b>\n\n"
                    f"👤 <b>From User:</b> <code>{user_msg['user_id']}</code> ({safe_user_name})\n"
                    f"⏰ <b>Query Sent:</b> <code>{sent_time}</code>\n"
                    f"💬 <b>Message:</b> <blockquote>{safe_user_message}</blockquote>"
                    f"---"
                )

                if not admin_reps:
                    response_text += "\n\n<i>No replies have been sent for this query yet.</i>"
                else:
                    for i, reply in enumerate(admin_reps, 1):
                        # i have tested myself that upto 13 replies it can be displayed without any issue like max characters reached and formatting issues
                        if i > 13: 
                            response_text += "\n\nThere are <b>some more replies left</b> but the message got too long, so please check the database yourself."
                            break
                        
                        reply_time = reply['action_time_replied'].strftime('%H:%M:%S on %Y-%m-%d')
                        admin_name = reply['admin_full_name'][:20] or "N/A"
                        admin_reply_text = reply['admin_reply_text'] if len(reply['admin_reply_text']) <= 100 else reply['admin_reply_text'][0:96]+ "..."
                        safe_admin_name = html.escape(admin_name)
                        safe_admin_reply_text = html.escape(admin_reply_text)
                        response_text += (
                            f"\n\n↪️ <b>Reply #{i}</b>\n"
                            f"  - <b>By Admin:</b> <code>{reply['admin_id']}</code> ({safe_admin_name})\n"
                            f"  - <b>Replied at:</b> <code>{reply_time}</code>\n"
                            f"  - <b>Reply:</b> <blockquote>{safe_admin_reply_text}</blockquote>"
                        )
                
                buttons = [[Button.inline("⬅️ Back", f"contact_back_{contact_id_str}_{admin_msg_id_str}")]]
                await event.edit(response_text, buttons=buttons, parse_mode='html', link_preview=False)

            elif data.startswith("contact_back_"):
                await event.answer()
                # This allows the owner to go back to the initial confirmation prompt
                *_, contact_id_str, admin_msg_id_str = data.split("_")
                previous_replies = db.get_previous_replies(int(contact_id_str))
                
                prompt_text = f"⚠️ **This query has already been handled {len(previous_replies)} time(s).**\n\nAre you sure you want to send another reply?"
                buttons = [
                    [Button.inline("✔️ Yes, reply again", f"contact_force_reply_{contact_id_str}_{admin_msg_id_str}")],
                    [Button.inline("❌ Cancel", "contact_cancel_reply")],
                    [Button.inline("🔍 Show Reply Details", f"contact_details_{contact_id_str}_{admin_msg_id_str}")]  
                ]
                await event.edit(prompt_text, buttons=buttons)

            elif data == "contact_cancel_reply":
                await event.edit("❌ Action cancelled. The reply was not sent.")

            elif data.startswith("cancel_session_"):
                try:
                    *_, flow_val, sid = data.split("_", 3)
                    flow = Flow(flow_val)
                    session = await self.session_manager.get(user_id, flow, sid)
                    session_active = session and session.active
                        
                    msg = await event.get_message()
                    text = msg.message
                    text_to_remove = None
                    buttons = msg.buttons

                    for row in buttons:
                        for btn in row:
                            if btn.data and btn.data.decode() == data:
                                row.remove(btn)
                                text_to_remove = btn.text.replace("❌ Cancel: ", "")
                                break
                        if not row:
                            buttons.remove(row)
                    
                    if session_active:
                        await self.session_manager.expire(user_id, flow, sid)
                        await event.answer(f"✅ Cancelled {text_to_remove}")
                        # if only clear all button is there but all actions have been already cleared individually
                        if len(buttons)==1:
                            await event.edit("✅ All pending actions have been cancelled.")
                        else:
                            final_text = text.replace(text_to_remove, "❌ Cancelled", 1)
                            await event.edit(text=final_text, buttons=buttons)
                    else:
                        await event.answer("⛔ This action has already expired or been cancelled.")
                        # if only clear all button is there but all actions have been already cleared individually
                        if len(buttons)==1:
                            await event.edit("✅ All pending actions have been cancelled.")
                        else:
                            final_text = text.replace(text_to_remove, " Already expired/cancelled", 1)
                            await event.edit(text=final_text, buttons=buttons)

                except Exception as e:
                    logger.error(f"Error cancelling session from callback: {e}")
                    await event.answer("❌ Could not cancel this action.", alert=True)

            elif data == "cancel_all_input_sessions":
                await event.answer("🧹 Cancelling all pending inputs...")
                active_sessions_with_flow = await self._get_active_input_sessions(user_id)
                
                if not active_sessions_with_flow:
                    await event.edit("✅ No pending actions to cancel.")
                    return
                    
                cancelled_count = 0
                for session, flow in active_sessions_with_flow:
                    await self.session_manager.expire(user_id, flow, session.session_id)
                    cancelled_count += 1
                    
                await event.edit(f"✅ Cancelled {cancelled_count} pending action(s).")


            # gstats command button handlers
            elif data.startswith("gstats_"):
                if not db.is_owner(user_id):
                    await event.answer("You are not authorized to perform this action.", alert=True)
                    return
                await event.answer()

                action = data.split("_", 1)[1]

                if action == "refresh":
                    message, buttons = await self._get_gstats_message_and_buttons()
                    try:
                        await event.edit(message, buttons=buttons)
                    except Exception as e:
                        logger.debug(f"Ignoring gstats refresh error (likely not modified): {e}")
                        pass

                if action == "premium":
                    users = db.get_gstats_premium_list()
                    content = ""
                    for user in users:
                        expiry = user['expiry_date'].strftime('%Y-%m-%d %H:%M')
                        content += f"• <code>{user['user_id']}</code> (@{user['username'] or 'N/A'}) - Expires: <code>{expiry}</code>\n"
                    await self._gstats_send_list(event, "Active Premium Members", content, "premium_users.txt")

                elif action == "top_users":
                    users = db.get_gstats_top_users()
                    content = ""
                    for i, user in enumerate(users, 1):
                        content += f"{i}. <code>{user['user_id']}</code> ({html.escape(user['full_name'])}) - <b>{user['total_requests']}</b> requests\n"
                    await self._gstats_send_list(event, "Top 50 Users by Requests", content, "top_users.txt")

                elif action == "admins":
                    users = db.get_gstats_admins_list()
                    content = ""
                    for user in users:
                        content += f"• <code>{user['user_id']}</code> (@{user['username'] or 'N/A'})\n"
                    await self._gstats_send_list(event, "Admins List", content, "admins.txt")

                elif action == "banned":
                    users = db.get_gstats_banned_list()
                    content = ""
                    for user in users:
                        ban_date = user['ban_date'].strftime('%Y-%m-%d')
                        content += f"• <code>{user['user_id']}</code> - Banned on <code>{ban_date}</code>\n  Reason: {html.escape(user['reason'])}\n\n"
                    await self._gstats_send_list(event, "Banned Users List", content, "banned_users.txt")

                elif action == "back":
                    message, buttons = await self._get_gstats_message_and_buttons()
                    await event.edit(message, buttons=buttons)

            elif data == "cancel_refresh_prompt":
                await event.answer()
                await event.edit("To cancel the ongoing refresh and clear all pending refresh jobs from the queue, please send the command: /cancelrefresh")

            elif data == "cancel_addcache_prompt":
                await event.answer()
                await event.edit("To cancel the ongoing add-cache operation and clear all pending add jobs from the queue, please send the command: /canceladdcache")

            # Handle various confirmations
            if data.startswith(("confirm_action_", "cancel_action_")):
                if not db.is_owner(user_id):
                    await event.answer("You are not authorized to perform this action.", alert=True)
                    return
                
                await event.answer()

                action, _, action_id = data.split("_", 2)
                if action_id not in self.pending_actions:
                    await event.edit("This action has expired or is invalid.")
                    return

                if action == "cancel":
                    del self.pending_actions[action_id]
                    await event.edit("✅ Action cancelled.")
                    return

                # If action is "confirm"
                pending_action = self.pending_actions.pop(action_id)

                action_type = pending_action['action_type']

                if action_type in ('broadcast', 'send'):
                    target_ids = pending_action['target_ids']
                    message_to_send = pending_action['message_to_send']
                    text_to_send = pending_action['text_to_send']
                    no_forward = pending_action['no_forward']
                    silent = pending_action['silent']

                    await event.edit(f"🚀 Starting {action_type} to {len(target_ids)} users...")

                    success_count = 0
                    fail_count = 0

                    for target_id in target_ids:
                        try:
                            if text_to_send:
                                await self.client.send_message(target_id, text_to_send, link_preview=False, silent=silent)
                            elif no_forward:
                                await self.client.send_message(target_id, message_to_send, silent=silent)
                            else:
                                await self.client.forward_messages(target_id, message_to_send, silent=silent)
                            success_count += 1
                        except Exception as e:
                            fail_count += 1
                            logger.warning(f"Failed to send message to user {target_id}: {e}")
                        await asyncio.sleep(0.1)

                    # Logging
                    flags_for_db = ""
                    if no_forward:
                        flags_for_db += "-nf"
                    if silent:
                        flags_for_db += "-s"
                    if not flags_for_db:
                        flags_for_db += "none"
                    
                    is_forward = False
                    fwd_chat_id, fwd_msg_id = None, None
                    message_for_db = text_to_send or self._get_message_content_for_db(message_to_send)

                    if message_to_send and message_to_send.forward:
                        is_forward = True
                        if from_peer := getattr(message_to_send.forward, 'from_id', None):
                            fwd_chat_id = getattr(from_peer, 'channel_id', None) or getattr(from_peer, 'chat_id', None) or getattr(from_peer, 'user_id', None)
                        fwd_msg_id = getattr(message_to_send.forward, 'channel_post', None)

                    if action_type == 'broadcast':
                        db.log_broadcast(user_id, message_for_db, flags_for_db, len(target_ids), success_count, fail_count, is_forward, fwd_chat_id, fwd_msg_id)
                    elif action_type == 'send':
                        db.log_send(user_id, message_for_db, flags_for_db, target_ids, success_count, fail_count, is_forward, fwd_chat_id, fwd_msg_id)

                    await event.edit(
                        f"✅ **{action_type.capitalize()} Complete!**\n\n"
                        f"• Sent to: `{success_count}` users\n"
                        f"• Failed for: `{fail_count}` users"
                    )
                    return
                
                elif action_type == 'clearcache_all':
                    packs_to_clear = pending_action['payload']['packs_to_clear']
                    await event.edit(f"🗑️ Deleting all {len(packs_to_clear)} cached packs from Telegram channels...")
                    
                    success = await self.delete_multiple_cache(packs_to_clear)
                    if success:
                        logger.info(f"Cache Cleared! Successfully deleted {len(packs_to_clear)} packs from cache channels.")
                        await event.edit(f"✅ **Cache Cleared!**\nSuccessfully deleted **{len(packs_to_clear)}** packs from cache channels.")
                    if success is False:
                        logger.error(f"Failed to delete cached packs from cache channels.")
                        await event.edit(f"❌ **Failed to Clear Cache!**\nDeletion of some cached packs from the cached channels failed.")
                    if success is None:
                        logger.warning(f"Cache is empty, still got a request seems something slipped off.")
                        await event.edit(f"**Nothing to clear**\nSeems there is nothing to clear in cache. But technically you shouldn't have reached here. 🤔")
                    return

                elif action_type == 'clearcache_packs':
                    pack_short_names = pending_action['payload']['pack_short_names']
                    await event.edit(f"Processing {len(pack_short_names)} packs to clear from cache...")

                    success_list = []
                    fail_list = []

                    for name in pack_short_names:
                        set_id = db.get_set_id_by_short_name(name)
                        if not set_id:
                            fail_list.append(f"• `{name}` (Not found)")
                            continue
                        
                        position = db.remove_from_cache(set_id)
                        if position:
                            channel_id, message_ids = position
                            try:
                                await self.client.delete_messages(channel_id, message_ids)
                                success_list.append(f"• `{name}`")
                                await asyncio.sleep(0.5) # Rate limit buffer
                            except Exception as e:
                                logger.error(f"Failed to delete messages for pack {name} set ID {set_id} from channel {channel_id}: {e}")
                                fail_list.append(f"• `{name}` (Deletion failed)")
                        else:
                            fail_list.append(f"• `{name}` (Not in cache)")
                    
                    logger.info(f"Cache clear complete! Successfully Cleared: {len(success_list)} Failed/Not Found: {len(fail_list)}")
                    response_message = "✅ **Cache Clearing Complete!**\n\n"
                    if success_list:
                        response_message += f"**Successfully Cleared:**\n" + "\n".join(success_list) + "\n\n"
                    if fail_list:
                        response_message += f"**Failed / Not Found:**\n" + "\n".join(fail_list)
                    
                    await event.edit(response_message)
                    return
                
                elif action_type in ("refreshcache_top_n", "refreshcache_links"):
                    self.active_refresh_message = await event.edit("🚀 **Starting Cache Refresh...**\nThis may take a moment to prepare.", buttons=None)
                    original_event = pending_action['original_event']
                    asyncio.create_task(self._execute_refresh_task(action_type, pending_action['payload'], original_event))
                    return

                elif action_type in ("addcache_all", "addcache_n", "addcache_links"):
                    self.active_add_message = await event.edit("🚀 **Starting Add-Cache...**\nThis may take a moment to prepare.", buttons=None)
                    original_event = pending_action['original_event']
                    asyncio.create_task(self._execute_addcache_task(action_type, pending_action['payload'], original_event))
                    return

                elif action_type == "addcache_interactive":
                    session = await self.session_manager.create(
                        user_id=user_id,
                        flow=Flow.ADDCACHE,
                        state='awaiting_addcache_input',
                        single_active=True,
                        ttl_seconds=7200 # 2 hours
                    )
                    await event.edit(
                        "✅ **Interactive Add-Cache Mode is active.**\n\n"
                        "Send sticker packs to add them to the cache. Reply to this message or send them directly.\n"
                        "Send /done when you are finished.", 
                        buttons=None
                    )
                    await self.session_manager.mark_message(user_id, Flow.ADDCACHE, session.session_id, event.chat_id, event.message_id)
                    return
                
            elif data.startswith("suggest_"):
                await event.answer()
                list_type = data.split('_', 1)[1] # 'daily' or 'all_time'
                
                try:
                    message, buttons = self._format_suggestion_message(list_type)
                    await event.edit(message, buttons=buttons, parse_mode='html', link_preview=False)
                except Exception as e:
                    logger.warning(f"Could not edit the suggest message: {e}")

            elif data.startswith("customize_"):

                parts = data.split("_",2)
                action = parts[1]
                sid = parts[2]

                session = await self.session_manager.get(user_id, Flow.CUSTOMIZE, sid)
                if not session or not session.active:
                    await event.edit("This customization session has expired. Please send the sticker again.", buttons=None)
                    return
                
                payload = session.payload

                if action == "title":
                    await self.session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_custom_title', ttl_seconds=3600)
                    await event.edit(
                        "Okay, send me the new **title** for your sticker pack (max 50 characters).",
                        buttons=[[Button.inline("⬅️ Back", f"customize_back_{sid}")]]
                    )
                    await self.session_manager.mark_message(user_id, Flow.CUSTOMIZE, sid, event.chat_id, event.message_id)

                elif action == "author":
                    await self.session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_custom_author', ttl_seconds=3600)
                    await event.edit(
                        "Sure, send me the **author name** you'd like to use (max 30 characters).",
                        buttons=[[Button.inline("⬅️ Back", f"customize_back_{sid}")]]
                    )
                    await self.session_manager.mark_message(user_id, Flow.CUSTOMIZE, sid, event.chat_id, event.message_id)

                elif action == "back":
                    await event.answer()
                    await self.session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_customization_choice', ttl_seconds=3600)
                    await self._update_customization_prompt(user_id, session)

                elif action == "cancel":
                    await self.session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                    await event.edit("❌ Conversion cancelled.", buttons=None)

                elif action == "convert":
                    # We need the original event object for the queue manager
                    asyncio.create_task(self.delete_multiple_messages(event.chat_id, payload["failed_inputs"], "Failed to delete invalid customization input messages.")) # clear any invalid input messaages

                    current_queue_count = await queue_manager.get_user_queue_count(user_id)
                    limit = MAX_CONCURRENT_PREMIUM_REQUESTS

                    # max queue limit
                    if current_queue_count >= limit:
                        message = (f"⏳ You've reached your limit!\n\n"
                                f"You currently have {current_queue_count}/{limit} items in the queue. "
                                f"Please wait for one to complete before adding more.")

                        await event.answer(message, alert=True)
                        return
                    await event.answer()

                    original_event = payload["original_event"]
                    sticker_set = payload['sticker_set']
                    if not (payload['custom_title'] or payload['custom_author']):
                        if self.cache_enabled and await self.check_cache(original_event, sticker_set):
                            await self.session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                            await event.delete()
                            return
                    
                    
                    await self._queue_sticker_pack(
                        original_event,
                        sticker_set,
                        is_premium=True,
                        custom_title=payload['custom_title'],
                        custom_author=payload['custom_author']
                    )
                    await self.session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                    await event.delete()
