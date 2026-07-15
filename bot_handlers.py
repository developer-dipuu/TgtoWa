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
from telethon.errors.rpcerrorlist import UserNotParticipantError, MessageDeleteForbiddenError
from telethon.events import StopPropagation
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.messages import SendReactionRequest, GetCustomEmojiDocumentsRequest
from telethon.tl.types import MessageEntityCustomEmoji, DocumentAttributeSticker, DocumentAttributeCustomEmoji, Message, ReactionEmoji, User
from telethon.extensions import html as telethon_html
from typing import Optional, Sequence, List, Dict, Any

from config import *
from utils import *
from queue_manager import queue_manager, SYSTEM_PRIORITY, REGULAR_USER_PRIORITY, PREMIUM_USER_PRIORITY
from sticker_converter import StickerConverter
from session_manager import session_manager, Flow, Session
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
        self.shutting_down = False
        self.cache_full_notified = False
        self.client = client
        self.network_task = NetworkTask(self.client)
        self.converter = StickerConverter(self.client)
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
        self.START_BUTTONS = [
            [Button.inline("Premium", b"premium", style="danger", icon=5967522716062847679), Button.inline("Help", b"help", style="success", icon=5818947586702184246)],
            [Button.url("Support Group", SUPPORT_GROUP_LINK, style="primary", icon=5895457880710058528), Button.inline("Commands", b"commands", style="primary", icon=5787544344906959608)]
        ]
        #background tasks for cleanup
        asyncio.create_task(self._reply_locks_cleanup_loop(ttl_seconds=3600))
        asyncio.create_task(self._db_cleanup_loop())
        asyncio.create_task(self._premium_users_cleanup_loop(check_interval_seconds=86400))
        asyncio.create_task(self._calculate_popular_packs_loop())
        asyncio.create_task(self._callback_locks_cleanup_loop(ttl_seconds=3600, check_interval_seconds=600))
        asyncio.create_task(self._daily_backup_loop())
        
    def check_banned(func):
        """Decorator to check if a user is banned before executing a command."""
        async def wrapper(self, event):
            if await db.is_banned(event.sender_id):
                logger.warning(f"Banned user {event.sender_id} tried to use the bot.")
                raise StopPropagation # Ignore
            return await func(self, event)
        return wrapper

    def register_handlers(self):
        """
        Registers all event handlers with the Telethon client.
        """
        username_regex = self.bot_username.lstrip('@')
        
        # user commands (Private)
        self.client.add_event_handler(self.start_command, events.NewMessage(pattern='/start', func=lambda e: e.is_private))
        self.client.add_event_handler(self.help_command, events.NewMessage(pattern='/help', func=lambda e: e.is_private))
        self.client.add_event_handler(self.queue_command, events.NewMessage(pattern='/queue', func=lambda e: e.is_private))
        self.client.add_event_handler(self.mystats_command, events.NewMessage(pattern='/mystats', func=lambda e: e.is_private))
        self.client.add_event_handler(self.premium_command, events.NewMessage(pattern='/premium', func=lambda e: e.is_private))
        self.client.add_event_handler(self.commands_command, events.NewMessage(pattern='/commands', func=lambda e: e.is_private))
        self.client.add_event_handler(self.contact_command, events.NewMessage(pattern='/contact', func=lambda e: e.is_private))
        self.client.add_event_handler(self.suggest_command, events.NewMessage(pattern='/suggest', func=lambda e: e.is_private))
        self.client.add_event_handler(self.id_command, events.NewMessage(pattern=r'/id(?:$|\s.*)', func=lambda e: e.is_private))

        # Group Handlers
        # self.client.add_event_handler(self.suggest_command, events.NewMessage(pattern='/suggest@'+ username_regex + r'(?:$|\s.*)', func=lambda e: not e.is_private))
        self.client.add_event_handler(self.help_command, events.NewMessage(pattern='/help@'+ username_regex + r'(?:$|\s.*)', func=lambda e: not e.is_private))

        # Restricted commands in groups (Redirect to DM)
        restricted_cmds = ['start', 'queue', 'mystats', 'premium', 'commands', 'suggest', 'contact']
        for cmd in restricted_cmds:
            self.client.add_event_handler(self.restricted_command_handler, events.NewMessage(pattern=rf"/{cmd}@{username_regex}(?:$|\s.*)", func=lambda e: not e.is_private))

        # owner commands
        self.client.add_event_handler(self.promote_command, events.NewMessage(pattern=r'/promote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.demote_command, events.NewMessage(pattern=r'/demote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.broadcast_command, events.NewMessage(pattern=r'/broadcast(?:$|\s.*)', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.broadcast_command, events.NewMessage(pattern=r'/broadcast@' + username_regex + r'(?:$|\s.*)', func=lambda e: not e.is_private and db.is_owner(e.sender_id)))
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
        self.client.add_event_handler(self.getjunk_command, events.NewMessage(pattern='/getjunk', func=lambda e: e.is_private and db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.clearjunk_command, events.NewMessage(pattern='/clearjunk', func=lambda e: e.is_private and db.is_owner(e.sender_id)))

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

    async def _db_cleanup_loop(self, check_interval_seconds: int = 3600):
        """Periodically cleans up old data from the database."""
        while True:
            await asyncio.sleep(check_interval_seconds)
            try:
                # The managers handle all the cleanup logics
                await session_manager.cleanup()
                await queue_manager.cleanup_queue()
            except Exception as e:
                logger.error(f"FATAL: The _db_cleanup_loop crashed: {e}", exc_info=True)
                await self.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                # Wait a while before retrying if it crashes
                await asyncio.sleep(3600)

    async def _premium_users_cleanup_loop(self, check_interval_seconds: int = 86400):
        """Periodically cleans up expired premium users from the database."""
        while True:
            # Wait for the next interval
            await asyncio.sleep(check_interval_seconds)
            try:
                logger.info("Running scheduled cleanup of expired premium users...")
                removed_count = await db.remove_expired_premium_users()
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
            self.daily_popular_packs = await db.get_popular_packs('daily')
            self.all_time_popular_packs = await db.get_popular_packs('all_time')
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
                # target for 23:59:00 UTC today
                next_run = now.replace(hour=23, minute=59, second=0, microsecond=0)
                
                # if the time is already past 23:59 then set it to 23:59 of the next day
                if now >= next_run:
                    next_run += timedelta(days=1)
                
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
            await db.calculate_and_store_popular_packs()
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
                await db.calculate_and_store_popular_packs()
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
            row.append(Button.url(f"{name1}", url=link1, style="primary", icon=None))

            # Second Button in Row (if it exists)
            if i + 1 < len(REQUIRED_CHANNELS_FORMATTED):
                name2, link2 = REQUIRED_CHANNELS_FORMATTED[i+1][:2]
                row.append(Button.url(f"{name2}", url=link2, style="primary", icon=None))
            
            keyboard.append(row)
        
        keyboard.append([Button.inline("Check Again", b"check_membership", style="success", icon=5258200019495821936)])
        return keyboard
    
    async def react(self, event: events.NewMessage.Event| None = None, chat_id: int | None = None, msg_id: int | None = None, emoji: str = "👍", big: bool = False) -> bool:
        if not event and not (chat_id and msg_id):
            raise ValueError("You must provide either an event or both chat_id and msg_id")
        if event:
            chat_id=  event.chat_id
            msg_id = event.message.id
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

    async def _safe_reply(self, event, *args, **kwargs):
        """Safely replies to an event, catching and logging any errors."""
        try:
            return await event.reply(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to reply in background: {e}")
            return None
    
    async def get_cache_channel(self):
        cache_channel = await db.get_or_create_cache_channel()
        if cache_channel: return cache_channel
        if not self.cache_full_notified:
            asyncio.create_task(self.notification_manager.send_cache_full_notification())
            self.cache_full_notified = True
        return None
    
    async def delete_cache(self, set_id) -> bool | None:
        """
        Attempts to remove a pack from the cache.
        If TG deletion fails (e.g., msg > 48h old), it logs the files as junk.
        Returns:
            - True: Successful deletion from DB and TG.
            - False: Deletion failed from TG, files logged as junk.
            - None: Pack was not found in the cache DB.
        """
        position = await db.remove_from_cache(set_id) 
        if position:
            channel_id, message_ids = position
            try:
                await self.client.delete_messages(channel_id, message_ids)
            except MessageDeleteForbiddenError as e:
                logger.warning(f"Could not delete messages for set {set_id}. They are > 48h old. Logging as junk.")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, "messages are > 48h old")
                return False
            except ChatAdminRequiredError as e:
                logger.error(f"Could not delete cache messages for set {set_id}. Insufficient permissions in the channel {channel_id}!")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, "insufficient permissions")
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
            except Exception as e:
                logger.error(f"Could not delete cache messages for set {set_id}. Error: {e}")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, str(e))
                asyncio.create_task(self.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
        else:
            return None
        return True
    
    async def delete_multiple_cache(self, set_ids: List[int]) -> dict:
        """
        Deletes multiple packs from cache by iterating over them.
        Returns a dictionary with counts of success/failure.
        """
        if not set_ids:
            return {"succeeded": 0, "failed": 0, "not_found": 0}

        results = {"succeeded": 0, "failed": 0, "not_found": 0}

        for set_id in set_ids:
            status = await self.delete_cache(set_id)
            if status is True:
                results["succeeded"] += 1
            elif status is False:
                results["failed"] += 1
            else: # None
                results["not_found"] += 1
            await asyncio.sleep(0.5) # to be nice to Telegram API

        return results
    
    async def delete_message(self, chat_id: int, msg_id: int | Sequence[int], custom_error_log: str | None = None):
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

    async def _get_pack_input_from_event(self, event: events.NewMessage.Event) -> Any | None:
        """
        Parses an event to find sticker pack input from text, links, stickers, or custom emoji.
        Returns the pack input (e.g., stickerset, short_name) or None if not found.
        """
        pack_input = None
        
        if event.text:
            # if a text 
            done = False
            if event.message.entities: # if it has custom emojis
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        emoji_docs = await self.client(GetCustomEmojiDocumentsRequest(document_id=[entity.document_id]))
                        if not emoji_docs:
                            break
                        # first_emoji_doc = emoji_docs[0]
                        # print(first_emoji_doc.stringify())
                        for attribute in emoji_docs[0].attributes:
                            if isinstance(attribute, DocumentAttributeCustomEmoji):
                                pack_input = attribute.stickerset
                                done=True
                                break
                        break
            if not done: # assume its a link 
                pack_input = extract_pack_name_from_url(event.text)
                if not pack_input:
                    await event.reply(
                        "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> <b>Invalid input!</b>\n\n"
                        "Please send a valid Telegram sticker or emoji pack link, "
                        "or forward a sticker/emoji from the pack you want to convert.",
                        parse_mode="html"
                    )

        elif event.sticker:
            # if its a sticker
            for attr in event.sticker.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    pack_input = attr.stickerset
                    break
            
            if not pack_input:
                await event.reply(
                    "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> This sticker doesn't seem to belong to a pack I can access.\n\nPlease forward a sticker from a public sticker pack.",
                    parse_mode="html"
                )
        else:
            # if not a text or sticker
            await event.reply(
                    "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> <b>Invalid input!</b>\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert.",
                    parse_mode="html"
                )

        return pack_input

    async def restricted_command_handler(self, event: events.NewMessage.Event):
        """
        Handles commands in groups by directing the user to DM.
        """
        # Extract the command, e.g., '/queue'
        text = event.raw_text.split()[0].split('@')[0]
        command = text.lstrip('/')
        
        bot_username_simple = self.bot_username.lstrip('@')
        deep_link = f"https://t.me/{bot_username_simple}?start={command}"
        message = ""
        match command:
            case 'start':
                message = "<tg-emoji emoji-id='5413694143601842851'>👋</tg-emoji> Hey there, I'm here to help you convert Telegram stickers and emoji packs to WhatsApp stickers!\n\nClick the button below to get started."
                buttons = [
                    [Button.url("Get Started", deep_link, style="primary", icon=5793933761594789855)]
                ]
            case 'queue':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to see your queue position in private chat."
                buttons = [
                    [Button.url("Get Queue Position", deep_link, style="primary", icon=5258513401784573443)]
                ]
            case 'mystats':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to see your stats in private chat."
                buttons = [
                    [Button.url("Get My Stats", deep_link, style="primary", icon=5431577498364158238)]
                ]
            case 'premium':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to see premium info in private chat."
                buttons = [
                    [Button.url("Premium Info", deep_link, style="primary", icon=5967522716062847679)]
                ]
            case 'commands':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to see available commands in private chat."
                buttons = [
                    [Button.url("Available Commands", deep_link, style="primary", icon=5787544344906959608)]
                ]
            case 'contact':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to use it in private chat."
                buttons = [
                    [Button.url("Contact", deep_link, style="primary", icon=5895457880710058528)]
                ]
            case 'suggest':
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to see the most popular sticker packs in private chat."
                buttons = [
                    [Button.url("Most Popular Sticker Packs", deep_link, style="primary", icon=6284845886417669247)]
                ]
            case _:
                message = "<tg-emoji emoji-id='5305381957524272531'>❌</tg-emoji> This command is not available in groups.\n\nPlease click the button below to use it in private chat."
                buttons = [
                    [Button.url("Continue in DM", deep_link, style="primary", icon=5793933761594789855)]
                ]
        
        await event.reply(message, buttons=buttons, parse_mode="html")

        raise StopPropagation

    async def check_cache(self, chat_id, sender_id, msg_to_reply_id, sticker_set_info, log_id: Optional[int] = None) -> bool:
        """Checks if the sticker set is cached or not if cached it will diresticker_setctly send those files and return True,
        else it will handle cache inconsistencies if any and return False"""
        user_id = sender_id
        set_id = sticker_set_info['set_id']
        current_title = sticker_set_info['title']
        current_sticker_count = len(sticker_set_info['doc_info'])

        # Check if the pack is cached and up-to-date
        cache_status, channel_id, message_ids = await db.is_pack_cached(set_id, current_title, current_sticker_count)
        
        # --- hehe cache hit ---
        if cache_status == 'hit':
            # Verify the cached files actually exist
            if None not in await self.client.get_messages(channel_id, ids=message_ids):
                await db.record_cache_hit(set_id)
                logger.info(f"✅ Cache hit for pack {set_id} in channel {channel_id}. Forwarding to user {user_id}.")
                num_packs = len(message_ids)
                
                # We need to log this as a successful conversion even though its from cache
                is_emoji_pack = sticker_set_info['is_emoji']
                pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
                pack_url = f"https://t.me/{pack_type_url}/{sticker_set_info['short_name']}"
                if log_id is None:
                    log_id = await db.log_conversion_request(user_id, set_id, pack_url, is_emoji_pack)
                
                await self.client.send_message(chat_id, f"<tg-emoji emoji-id='5456140674028019486'>⚡️</tg-emoji> Found this pack in the cache! Sending <b>{num_packs}</b> {'file' if num_packs == 1 else 'files'} instantly...", reply_to=msg_to_reply_id, parse_mode="html")

                try:
                    messages = await self.client.get_messages(channel_id, ids=message_ids)
                    for message in messages:
                        await self.client.send_message(entity=chat_id, message=message, link_preview=False)

                    logger.info(f"✅ Successfully forwarded pack {set_id} from cache to user {user_id}.")
                    await self.client.send_message(chat_id, "<tg-emoji emoji-id='5872922883092648417'>📱</tg-emoji> To import to WhatsApp, use '<b>Sticker Maker</b>' app on your phone (/help for more info). Enjoy!", parse_mode="html")
                    await db.update_conversion_log(log_id, "completed_from_cache", datetime.now(timezone.utc), 0.0)
                    return True
                except UserIsBlockedError:
                    # some dumbass block the bot even when it is sending files
                    logger.error(f"User has blocked the bot! Failed to forward cached messages for pack {set_id} to user {user_id}.")
                    await db.update_conversion_log(log_id, "completed_from_cache_but_blocked", datetime.now(timezone.utc), 0.0)
                    return True
                # if all successful upload
                except Exception as e:
                    logger.error(f"Failed to forward cached messages for pack {set_id} to user {user_id}: {e}")
                    # If forwarding fails, it's a critical error. Let's treat it as a cache miss and re-convert.
                    await self.client.send_message(chat_id, "Oops! I found this in the cache, but couldn't send it. I'll try re-converting it for you now. <tg-emoji emoji-id='5384307092599348179'>🫡</tg-emoji>", reply_to=msg_to_reply_id, parse_mode="html")
                    await db.update_conversion_log(log_id, "failed_forward_from_cache", datetime.now(timezone.utc), 0.0)
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

        user_flows = await session_manager.get_all_user_sessions(user_id)
        for flow_val, sessions_list in user_flows.items():
            try:
                flow = Flow(flow_val) # Convert string from dict key back to Enum
                for session in sessions_list:
                    # We check if the session's state requires input
                    if session.state in INPUT_AWAITING_STATES:
                        active_sessions_with_flow.append((session, flow))
            except ValueError:
                logger.warning(f"Found session with unknown flow '{flow_val}' for user {user_id}")

        return active_sessions_with_flow
    
    async def _prompt_for_ambiguous_input(self, event: events.NewMessage.Event, sessions_with_flow: List[tuple[Session, Flow]]):
        """Notifies the user that their input is ambiguous and provides option to cancel."""

        all_ids_to_delete = {event.message.id}
        for session, _ in sessions_with_flow:
            old_prompt_ids = session.payload.get('ambiguity_prompt_ids', [])
            all_ids_to_delete.update(old_prompt_ids)

        if all_ids_to_delete:
            asyncio.create_task(self.delete_multiple_messages(
                event.chat_id,
                list(all_ids_to_delete),
                custom_error_log="Failed to delete old ambiguity prompts."
            ))

        text = (
            "<tg-emoji emoji-id='5472248119942979457'>🤔</tg-emoji> <b>Multiple Actions Pending</b>\n\n"
            "You have several actions waiting for your text input. "
            "To continue, please <b>scroll up and reply directly</b> to the correct prompt message or <b>cancel other actions</b> using the buttons below.\n\n"
            "Here are your pending actions:"
        )
        
        action_list = []
        buttons = []

        for session, flow in sessions_with_flow:
            payload = session.payload
            sid = session.session_id

            action_desc = "Unknown Action"
            button_text = ""
            if flow == Flow.CUSTOMIZE:
                pack_title = payload['sticker_set_info']['title']
                safe_pack_title = pack_title[:15] + "..." if len(pack_title) > 15 else pack_title
                if session.state == 'awaiting_custom_title':
                    action_desc = f"<tg-emoji emoji-id='5258215635996908355'>✏️</tg-emoji> Set Title for '{html.escape(safe_pack_title)}'"
                    button_text = f"Set Title for '{safe_pack_title}'"
                elif session.state == 'awaiting_custom_author':
                    action_desc = f"<tg-emoji emoji-id='5258011929993026890'>👤</tg-emoji> Set Author for '{html.escape(safe_pack_title)}'"
                    button_text = f"Set Author for '{safe_pack_title}'"
            elif flow == Flow.CONTACT:
                action_desc = "<tg-emoji emoji-id='5260535596941582167'>✉️</tg-emoji> Send Contact Message"
                button_text = "Send Contact Message"
            elif flow == Flow.ADDCACHE:
                action_desc = "<tg-emoji emoji-id='5258108352008823107'>➕️</tg-emoji> Add Cache Interactive"
                button_text = "Add Cache Interactive"

            action_list.append(f"{action_desc}")
            buttons.append([Button.inline(f"Cancel: {button_text}", f"cancel_session_{flow.value}_{sid}", style="danger", icon=5260342697075416641)])
        
        buttons.append([Button.inline("Cancel All Pending Actions", "cancel_all_input_sessions", style="danger", icon=5267123797600783095)])
    
        full_text = text + "\n" + "\n".join(action_list)
        prompt_msg = await event.respond(full_text, buttons=buttons, parse_mode="html")

        prompt_id = prompt_msg.id
        
        # Tag all the ambiguous sessions with the ID of the prompt we just sent
        # Why we are appending to the list instead of overriding as we have fired of delete tasks of old prompt ids?
        # because the messages are sent async so by the time next prompt arrive the old one might haven't been sent yet 
        # so appending make atleat the process_session_input (or when user sends ambiguous input slowly) delete all old prompts
        # otherwise it would have only deleted the last completed message but since async, multiple maybe sent if user inputs too fast
        for session, flow in sessions_with_flow:
            await session_manager.update(
                event.sender_id,
                flow,
                session.session_id,
                payload_mutator=lambda p, pid=prompt_id: p.setdefault('ambiguity_prompt_ids', []).append(pid)
            )

        raise StopPropagation

    async def _process_session_input(self, event: events.NewMessage.Event, session: Session, flow: Flow):
        """Routes a user's text message to the correct logic based on the session."""
        user_id = event.sender_id

        prompt_ids_to_delete = session.payload.get('ambiguity_prompt_ids')

        if prompt_ids_to_delete:
            asyncio.create_task(self.delete_multiple_messages(event.chat_id, prompt_ids_to_delete, "Failed to delete ambagious prompt."))
            
            # Clean the ambiguity_prompt_ids from all active sessions for this user
            all_active = await self._get_active_input_sessions(user_id)
            for active_session, active_flow in all_active:
                if 'ambiguity_prompt_ids' in active_session.payload:
                    await session_manager.update(
                        user_id,
                        active_flow,
                        active_session.session_id,
                        payload_mutator=lambda p: p.pop('ambiguity_prompt_ids', None)
                    )

        # --- CONTACT MESSAGE ---
        if flow == Flow.CONTACT and session.state == 'awaiting_contact_message':
            await session_manager.expire(user_id, Flow.CONTACT, session.session_id) # Expire after use

            message_content = self._get_message_content_for_db(event.message)
            contact_id = await db.log_contact_message(user_id, event.message.id, message_content)
            admin_ids = await db.get_all_admin_ids()

            user = await event.get_sender()
            user_display_name = get_user_display_name(user)
            role = "<tg-emoji emoji-id='5258165702707125574'>⭐</tg-emoji> Premium User" if await db.is_premium(user.id) else "<tg-emoji emoji-id='5316727448644103237'>👤</tg-emoji> Regular User"
            stats = await db.get_user_stats(user.id)

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

            single_success = False
            for admin_id in admin_ids:
                try:
                    await self.client.send_message(admin_id, header_message, parse_mode='html')
                    await self.client.forward_messages(admin_id, event.message)
                    single_success = True
                    logger.debug(f"Forwarded the {user.id} user's contact message to the admin {admin_id}")
                except Exception as e:
                    logger.warning(f"Failed to forward contact message to admin {admin_id}: {e}")
            
            if single_success:
                await event.reply(CONTACT_SUCCESS_MESSAGE, parse_mode='html')
            else:
                await event.reply(CONTACT_FAILURE_MESSAGE, parse_mode='html')
            raise StopPropagation

        # --- CUSTOMIZATION INPUT ---
        elif flow == Flow.CUSTOMIZE and session.state in ('awaiting_custom_title', 'awaiting_custom_author'):
            payload = session.payload

            if not event.text or not event.text.strip():
                await event.delete()
                msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Only valid <b>text messages</b> are allowed. Please try again.", parse_mode='html')
                await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id,
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                return

            user_input = event.text.strip()

            if session.state == 'awaiting_custom_title':
                if len(user_input) > 50:
                    await event.delete()
                    msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Title too long (max 50 chars). Please try again.", parse_mode='html')
                    await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, 
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                    return
                payload['custom_title'] = user_input

            elif session.state == 'awaiting_custom_author':
                if len(user_input) > 30:
                    await event.delete()
                    msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Author name too long (max 30 chars). Please try again.", parse_mode='html')
                    await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, 
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                    return
                payload['custom_author'] = user_input

            await asyncio.create_task(self.react(event, emoji= "🆒", big=True))
            messages_to_delete = payload.get("failed_inputs", [])
            payload['failed_inputs'] = []

            session.state = 'awaiting_customization_choice' # Go back to the main menu
            await session_manager.update(
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
        pack_input  = await self._get_pack_input_from_event(event)

        if not pack_input:
            return
        
        try:
            sticker_set = await self.network_task.get_sticker_set(pack_input)
            if not sticker_set or not sticker_set.documents:
                await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Couldn't find that sticker pack. It might be private or empty.", parse_mode='html')
                return

            # Perform a silent cache check
            set_id = sticker_set.set.id
            set_title = sticker_set.set.title
            set_count = len(sticker_set.documents)

            cache_status, channel_id, message_ids = await db.is_pack_cached(set_id, set_title, set_count)

            if cache_status == 'hit':
                messages = await self.client.get_messages(channel_id, ids=message_ids)
                if messages and all(msg is not None for msg in messages):
                    await db.record_cache_hit(set_id, is_system_process=True)
                    await event.reply(f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Pack '<code>{set_title}</code>' is already in the cache. Skipped.", parse_mode='html')
                    return
                else:
                    asyncio.create_task(self.delete_cache(set_id)) # Inconsistent cache
            elif cache_status == 'stale':
                asyncio.create_task(self.delete_cache(set_id))
            
            # Queue it
            placeholder = await event.reply(f"<tg-emoji emoji-id='5787344001862471785'>⏳</tg-emoji> Adding '<code>{set_title}</code>' to the queue...", parse_mode='html')
            system_id = SYSTEM_USER_ID

            is_emoji = sticker_set.set.emojis
            pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{sticker_set.set.short_name}"
            log_id = await db.log_conversion_request(system_id, set_id, pack_url, is_emoji)
            
            sticker_set_doc_mime_type = [doc.mime_type for doc in sticker_set.documents]
            sticker_set_info = {"set_id": sticker_set.set.id, "access_hash": sticker_set.set.access_hash, "short_name": sticker_set.set.short_name, "is_emoji": sticker_set.set.emojis, "doc_info": sticker_set_doc_mime_type, "title": sticker_set.set.title, }
            estimated_seconds = estimate_wait_time(sticker_set_info['doc_info'], None)
            
            position = await queue_manager.add_to_queue(
                user_id=system_id, chat_id=event.chat_id, message_id=event.message.id, username="System AddCache (Interactive)", bot_reply_message_id=placeholder.id,
                sticker_set_info=sticker_set_info, estimated_seconds=estimated_seconds, log_id=log_id,
                priority=SYSTEM_PRIORITY, is_cache_suspicious=False,
                is_silent_mode=True
            )
            self.active_add_jobs.add(log_id)
            await placeholder.edit(f"<tg-emoji emoji-id='6296577138615125756'>✅</tg-emoji> Queued '<code>{set_title}</code>' for caching at position {position}.", parse_mode='html')
            
            if not self.processing_lock.locked():
                queue_stats = await queue_manager.get_queue_stats()
                if not queue_stats["currently_processing"]:
                    asyncio.create_task(self.process_queue())

        except Exception as e:
            await event.reply(f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> An error occurred: {e}", parse_mode='html')
            logger.error(f"Interactive AddCache Error: {e}", exc_info=True)

    async def _start_customization_flow(self, event_info: dict, sticker_set_info: dict):
        """Sends the initial customization prompt to premium users."""
        user_id = event_info['user_id']
        payload = {
            "sticker_set_info": sticker_set_info,
            "original_event_info": event_info,
            "prompt_message_id": None,
            "custom_title": None,
            "custom_author": None,
            "failed_inputs": []
        }
        session = await session_manager.create(
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
        original_event_info = payload['original_event_info']
        title = html.escape(payload['custom_title'] or payload['sticker_set_info']['title'])
        author = html.escape(payload['custom_author'] or self.bot_username)
        
        text = (
            f"<tg-emoji emoji-id='5947363097353130662'>✨</tg-emoji> <b>Premium Customization</b> <tg-emoji emoji-id='5947363097353130662'>✨</tg-emoji>\n\n"
            f"Here's the current setup for your pack:\n"
            f"<blockquote><tg-emoji emoji-id='5258215635996908355'>✏️</tg-emoji> <b>Title</b>: <code>{title}</code></blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5258011929993026890'>👤</tg-emoji> <b>Author</b>: <code>{author}</code></blockquote>\n"
            f"Ready to go, or want to make a change?"
        )
        
        sid = session.session_id
        buttons = [
            [Button.inline("Set Title", f"customize_title_{sid}", style="primary", icon=5258215635996908355), Button.inline("Set Author", f"customize_author_{sid}", style="primary", icon=5258011929993026890)],
            [Button.inline("Convert Now", f"customize_convert_{sid}", style="success", icon=5260416304224936047)],
            [Button.inline("Cancel", f"customize_cancel_{sid}", style="danger", icon=5260342697075416641)]
        ]
        
        try:
            if not payload['prompt_message_id']:
                bot_message = await self.client.send_message(
                    entity=original_event_info['chat_id'],
                    message=text,
                    buttons=buttons,
                    parse_mode='html',
                    reply_to=original_event_info['message_id']
                )
                payload['prompt_message_id'] = bot_message.id
                await session_manager.update(user_id, Flow.CUSTOMIZE, sid, payload_mutator=lambda p: p.update(payload))
            else:
                await self.client.edit_message(
                    original_event_info['chat_id'],
                    payload['prompt_message_id'],
                    text,
                    buttons=buttons,
                    parse_mode='html'
                )
        except Exception as e:
            logger.warning(f"Failed to send customization prompt to user {user_id}: {e}")


    async def _queue_sticker_pack(self, event_info, sticker_set_info, is_premium, custom_title: Optional[str] = None, custom_author: Optional[str] = None):
        """Helper function to consolidate the logic for adding a pack to the queue."""
        user = await self.client.get_entity(event_info['user_id'])

        # find estimated time and user priority
        estimated_seconds = estimate_wait_time(sticker_set_info['doc_info'], None)
        priority = PREMIUM_USER_PRIORITY if is_premium else REGULAR_USER_PRIORITY
        # max conversion duration cap
        if not is_premium:
            if estimated_seconds > MAX_CONVERSION_SECONDS_REGULAR:
                await self.client.send_message(
                    entity=event_info['chat_id'],
                    message = (
                        "<tg-emoji emoji-id='5228947933545635555'>😟</tg-emoji> <b>Pack Too Large for Regular Users!</b>\n\n"
                        f"This pack is estimated to take more than <b>{MAX_CONVERSION_SECONDS_REGULAR // 60} minutes</b> to convert, "
                        "which exceeds the time limit for regular users.\n\n"
                        "Upgrade to <b>Premium</b> to convert larger packs instantly!\n"
                    ),
                    buttons=[[Button.inline("Learn about Premium", b"premium", style="primary", icon=5967522716062847679)]],
                    reply_to=event_info['message_id'],
                    parse_mode='html'
                )
                return
        set_id = sticker_set_info['set_id']
        is_emoji_pack = sticker_set_info['is_emoji']
        pack_display_name = sticker_set_info['title']

        # get the user's name pack url
        user_display_name = get_user_display_name(user)
        pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
        pack_url = f"https://t.me/{pack_type_url}/{sticker_set_info['short_name']}"

        # Log the request to the database
        log_id = await db.log_conversion_request(user.id, set_id, pack_url, is_emoji_pack)

        # NEW: Increment daily usage counter before adding to queue
        await db.increment_daily_requests(user.id)

        # send adding to queue message
        placeholder_message = await self.client.send_message(entity=event_info['chat_id'], message="<tg-emoji emoji-id='5220046725493828505'>⌛</tg-emoji> Adding to the queue...", reply_to=event_info['message_id'], parse_mode='html')

        # Determine if this pack is "cache suspicious"
        is_suspicious = not custom_title and not custom_author and self.cache_enabled and await queue_manager.is_set_id_queued(set_id)
        if is_suspicious:
            logger.info(f"Queueing pack {set_id} as 'cache suspicious'.")


        # add to queue and get position for this item
        position = await queue_manager.add_to_queue(
                user_id=user.id,
                chat_id=event_info['chat_id'],
                message_id=event_info['message_id'],
                username=user_display_name,
                bot_reply_message_id=placeholder_message.id,
                sticker_set_info=sticker_set_info,
                estimated_seconds=estimated_seconds,
                log_id=log_id,
                priority=priority,
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
                final_message_text = (f"<b><tg-emoji emoji-id='6080171114007367607'>⭐</tg-emoji> VIP Status Confirmed!</b>\n\n"
            f"Your pack: <b><a href=\"{pack_url}\">{safe_pack_name}</a></b> has been fast-tracked to position <b>{position}</b>.<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji>\n")

                if slots_left > 0:
                    final_message_text += f"<blockquote>As a premium user, you can still add <b>{slots_left}</b> more pack(s) to the queue. Keep 'em coming!</blockquote>\n"

                final_message_text += "\n<b>I'll notify you when the conversion starts!</b>"
            else:
                final_message_text = (f"<b><tg-emoji emoji-id='6296367896398399651'>✅</tg-emoji> Added to conversion queue!</b>\n\n"
                f"<tg-emoji emoji-id='5785045099142450328'>📦</tg-emoji> Pack: <a href=\"{pack_url}\">{safe_pack_name}</a>\n"
                f"<tg-emoji emoji-id='5821128296217185461'>📍</tg-emoji> Position: {position}\n\n"
                f"<blockquote>I'll notify you when the conversion starts!</blockquote>")

            # finally edit the message with detailed one
            await self.client.edit_message(
                entity=placeholder_message.chat_id,
                message=placeholder_message.id,
                text=final_message_text,
                buttons=[[Button.inline("Check Queue", b"check_queue", style="primary", icon=5258513401784573443)],[Button.inline("Cancel", data=f"cancel_item_{log_id}".encode(), style="danger", icon=5260342697075416641)]],
                link_preview=False, parse_mode='html'
            )

        if not self.processing_lock.locked():
            # Check if anyone is processing before starting a new process_queue task
            queue_stats = await queue_manager.get_queue_stats()
            is_processing = queue_stats["currently_processing"]
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
        if event.is_reply and await db.is_admin(user.id):
            if await self.handle_admin_reply(event): # if it was handled
                raise StopPropagation 

        # ---------- handle session based iput -------
        session_from_reply = None
        flow_from_reply = None
        if event.is_reply:
            session_from_reply_tuple = await session_manager.from_reply(event.chat_id, event.reply_to_msg_id)
            session_from_reply = None
            if session_from_reply_tuple:
                user_id, flow_val, session_id = session_from_reply_tuple
                flow_from_reply = Flow(flow_val)
                session_from_reply = await session_manager.get(user_id, flow_from_reply, session_id)

        if session_from_reply and session_from_reply.active:
            # User replied to a session message, process it directly
            await self._process_session_input(event, session_from_reply, flow_from_reply)
            return
        
        # if it wasnt a reply to a session message, check for any active input sessions
        active_sessions_with_flow = await self._get_active_input_sessions(user.id)

        if event.is_reply and len(active_sessions_with_flow) >= 1:
            # user replied to a wrong message or expired session
            await event.reply("<tg-emoji emoji-id='5915991028430542030'>❌</tg-emoji> The messsage you replied to is not a valid input action or has expired.", parse_mode='html')
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
        await db.add_or_update_user(user.id, user.username, get_user_display_name(user))
        # membership check
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
            return

        is_premium = await db.is_premium(user.id)

        # Daily conversion limit check
        daily_limit = DAILY_LIMIT_PREMIUM if is_premium else DAILY_LIMIT_REGULAR
        if daily_limit > 0: # A limit of 0 or less means unlimited.
            current_daily_usage = await db.get_daily_usage(user.id)
            if current_daily_usage >= daily_limit:
                message = (
                    f"<tg-emoji emoji-id='5418159410646099061'>🚫</tg-emoji> <b>Daily Limit Reached!</b>\n\n"
                    f"You have used your quota of <b>{current_daily_usage}/{daily_limit}</b> conversions for today. "
                    "Your limit will reset at midnight (UTC)."
                )
                buttons = None
                if not is_premium:
                    message += f"\n\nConsider upgrading to <b>Premium</b> for higher limits! Plans start from just <b>${PREMIUM_PRICE_MONTHLY}</b>/month."
                    buttons = [[Button.inline("Learn about Premium", b"premium", style="primary", icon=5967522716062847679)]]
                
                await event.reply(message, buttons=buttons, parse_mode='html')
                return
        
        limit = MAX_CONCURRENT_PREMIUM_REQUESTS if is_premium else MAX_CONCURRENT_REGULAR_REQUESTS

        # max queue limit
        async with self._user_processing_lock:
        # This is for some mfs who spam the bot for no reason,we better ban those shits after a few warning but let's see this in future
            current_queue_count = await queue_manager.get_user_queue_count(user.id)
            realistic_position = current_queue_count + (1 if user.id in self._users_adding_to_queue else 0)
            if realistic_position >= limit:
                buttons = None
                if is_premium:
                    message = (f"<tg-emoji emoji-id='5915991028430542030'>🚫</tg-emoji> <b>You've reached your limit!</b>\n\n"
                            f"You currently have <b>{realistic_position}/{limit}</b> items in the queue. "
                            f"Please wait for one to complete before adding more.")
                    buttons = [[Button.inline("Check Queue", b"check_queue", style="primary", icon=5258513401784573443)]]
                else:
                    message = (f"<tg-emoji emoji-id='5915991028430542030'>🚫</tg-emoji> You're already in the queue! Please wait for your current request to complete."
                            f"\n\nUpgrade to <b>Premium</b> to convert multiple packs <b>at the same time</b>!")
                    buttons = [[Button.inline("Check Queue", b"check_queue", style="primary", icon=5258513401784573443), 
                                Button.inline("Learn about Premium", b"premium", style="success", icon=5967522716062847679)]]

                asyncio.create_task(self._safe_reply(event, message, buttons=buttons, parse_mode='html'))
                return
            
            # if check passes mark user as adding to queue
            self._users_adding_to_queue.add(user.id)
        
        try:        
            # now time to extract pack details based on the type of message sent
            pack_input = await self._get_pack_input_from_event(event)
            if not pack_input:
                return
            # Fetch the sticker/emoji set to get its actual name and type
            try:
                sticker_set = await self.network_task.get_sticker_set(pack_input)

                if not sticker_set or not sticker_set.documents:
                    logger.error(f"Could not fetch a valid sticker set for input: {pack_input}")
                    await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> I couldn't find that sticker pack. It might be private, invalid, or empty. Please try another one!", parse_mode='html')
                    return

            except Exception as e:
                    logger.error(f"Error fetching set name for user {user.id}: {e}")
                    await event.reply("<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> An error occured while fetching the sticker pack. Please try again later!", parse_mode='html')
                    return
                    
            sticker_set_doc_mime_type = [doc.mime_type for doc in sticker_set.documents]
            sticker_set_info = {"set_id": sticker_set.set.id, "access_hash": sticker_set.set.access_hash, "short_name": sticker_set.set.short_name, "is_emoji": sticker_set.set.emojis, "doc_info": sticker_set_doc_mime_type, "title": sticker_set.set.title, }
            if is_premium:
                # For premium users we use special customizayion flow
                event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id}
                await self._start_customization_flow(event_info, sticker_set_info)
            else:
                # For regular users check cache and queue directly
                if self.cache_enabled and await self.check_cache(event.chat_id, event.sender_id, event.message.id, sticker_set_info): # cache hit
                    return
                # cache miss we got to queue it 
                event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id}
                await self._queue_sticker_pack(event_info, sticker_set_info, is_premium=False)

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
                    entity=item.chat_id,
                    message=item.bot_reply_message_id,
                    text=f"<tg-emoji emoji-id='5454074580010295588'>⌛</tg-emoji> Your request for the pack is now processing...",
                    buttons=None,
                    parse_mode='html'
                )
            except Exception as e:
                logger.warning(f"Could not edit message {item.bot_reply_message_id} to remove cancel button: {e}")

            status_message = await self.client.send_message(
                item.chat_id,
                "<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji> Starting conversion for your pack...\n"
                "<tg-emoji emoji-id='5382194935057372936'>🤔</tg-emoji> Estimated time: Calculating...",
                parse_mode='html'
            )
        # sticker info
        sticker_set = None
        try:
            sticker_set = await self.network_task.get_sticker_set(item.sticker_set_info['set_id'], access_hash=item.sticker_set_info['access_hash'])
        except Exception:
            raise
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
                entity=item.chat_id,
                message=status_message.id,
                text=f"<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji> Starting conversion for your pack...\n"
                    f"<tg-emoji emoji-id='5382194935057372936'>🤔</tg-emoji> Estimated time: {estimated_time_str}",
                parse_mode='html'
            )
            
            item_name = "emojis" if is_emoji_pack else "stickers"
            message = (f"<tg-emoji emoji-id='5339166917598916047'>📊</tg-emoji> <b>Pack Details:</b>\n"
                    f"<tg-emoji emoji-id='5787399776307776752'>◾️</tg-emoji> Name: <a href=\"{pack_url}\">{safe_pack_title}</a>\n"
                    f"<tg-emoji emoji-id='5787399776307776752'>◾️</tg-emoji> Total {item_name}: {total_stickers}\n"
                    f"<tg-emoji emoji-id='5787399776307776752'>◾️</tg-emoji> This will create {num_packs} .wastickers {'file' if num_packs == 1 else 'files'}.")
            await self.client.send_message(item.chat_id, message, parse_mode='html', link_preview=False)

        # run the conversion with a timeout (either 60 sec or 3x the estimated time)
        conversion_start_time = time.monotonic()
        try:
            wastickers_files = await asyncio.wait_for(self.converter.create_wastickers_pack(sticker_set, final_author, custom_title=final_title), timeout=processing_timeout)
        except asyncio.TimeoutError:
            status_for_db = "failed_conversion_timeout"
            logger.error(f"Conversion timed out while creating .wasticker files for user {item.user_id}. Log ID: {item.log_id}")
            if not is_silent_mode:
                try:
                    await self.client.send_message(
                        item.chat_id,
                        (f"<tg-emoji emoji-id='5258113901106580375'>⏱️</tg-emoji> The conversion for your pack <b>took longer than expected</b> and has <b>timed out</b>.<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji>\n\n"
                        f"It is generally due to Telegram server issues.\n"
                        f"<b>Please try again after some time or with a different pack.</b>\n\n"
                        f"If the problem persists, ping us at <b>{SUPPORT_GROUP}</b>"),
                        parse_mode='html'
                    )
                except Exception as e:
                    logger.warning(f"Could not send timeout message to {item.user_id}: {e}")
            user = await self.client.get_entity(item.user_id)
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "ConversionTimeout", "Creating .wasticker files took longer than expected.", sticker_set=sticker_set)
            return status_for_db # return failed status immidiately 
        except Exception as e:
            status_for_db = "failed_conversion_exception"
            logger.error(f"Conversion failed while creating .wasticker files for user {item.user_id}. Log ID: {item.log_id}")
            if not is_silent_mode:
                try:
                    await self.client.send_message(
                        item.chat_id,
                        (f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> The conversion for your pack has failed.\n"
                        f"Please try again later or with a different pack.\n\n"
                        f"If the problem persists, ping us at <b>{SUPPORT_GROUP}</b>"),
                        parse_mode='html'
                    )
                except Exception as e:
                    logger.warning(f"Could not send timeout message to {item.user_id}: {e}")
            user = await self.client.get_entity(item.user_id)
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, type(e).__name__, str(e), sticker_set=sticker_set)
            return status_for_db # return failed status immidiately 
    
        if not wastickers_files:
            status_for_db = "failed_no_wasticker_file"
            if not is_silent_mode:
                await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Failed to convert the pack <b><a href=\"{pack_url}\">{safe_pack_title}</a></b>. \nIf the problem persists, ping us at <b>{SUPPORT_GROUP}</b>", link_preview=False, parse_mode='html')
            # This is a failure so we raise an exception.
            user = await self.client.get_entity(item.user_id)
            user_display_name =get_user_display_name(user)
            await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "NoWastickerFileCreated", "The conversion returned no .wasticker file.", sticker_set=sticker_set)
            return status_for_db

        conversion_end_time = time.monotonic()

        conversion_duration = conversion_end_time - conversion_start_time


        # update the stats with duration
        new_cache_score = await db.add_or_update_sticker_set_stats(
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
        if (
            self.cache_enabled 
            and not item.custom_title 
            and not item.custom_author
            and (target_cache_channel:= await self.get_cache_channel())
        ):
            if not is_silent_mode:
                await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='6080182302397174299'>✅</tg-emoji> Conversion complete! Sending <b>{len(wastickers_files)}</b> {'file' if len(wastickers_files) == 1 else 'files'}...", link_preview=False, parse_mode='html')
            
            all_uploads_succeeded = True
            try:
                cached_messages = await self.network_task.upload_files(wastickers_files, pack_url, safe_pack_title, target_cache_channel)
                if not cached_messages or len(cached_messages) != len(wastickers_files): all_uploads_succeeded = False
                # Now, log this to our database
                if all_uploads_succeeded:
                    status_for_db = "completed"
                    cached_messages_id = [cached_message.id for cached_message in cached_messages]
                    await db.add_to_cache(sticker_set.set.id, new_cache_score, target_cache_channel, cached_messages_id)
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
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Timed out while uploading pack. Please try again later.", parse_mode='html')
                    else:
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Timed out while uploading pack part {first_failed_index+1}. Please try again later.", parse_mode='html')
                #notify owner
                user = await self.client.get_entity(item.user_id)
                user_display_name =get_user_display_name(user)
                await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "UploadTimeoutCaching", f"File: {', '.join(failed_uploads)}", sticker_set=sticker_set)
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
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Failed to upload pack due to an error. Please use <b>/contact</b> to report it to the admins.", parse_mode='html')
                    else:
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Failed to upload pack part {first_failed_index+1} due to an error. Please use <b>/contact</b> to report it to the admins.", parse_mode='html')
                #notify owner
                user = await self.client.get_entity(item.user_id)
                user_display_name =get_user_display_name(user)
                await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "UploadErrorCaching", f"File: {', '.join(failed_uploads)}", sticker_set=sticker_set)
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
                    asyncio.create_task(self.delete_multiple_messages(target_cache_channel, cached_message_ids, custom_log_msg))

            # ----- now send from cache (if not a system task) --------
            if not is_silent_mode and all_uploads_succeeded:
                try:
                    for message in cached_messages:
                        await self.client.send_message(entity=item.chat_id, message=message, link_preview=False)

                    await self.client.send_message(item.chat_id, "<tg-emoji emoji-id='5872922883092648417'>📱</tg-emoji> To import to WhatsApp, use an app like '<b>Sticker Maker</b>' on your phone (/help for more info). Enjoy!", parse_mode='html')
                    status_for_db = "completed"
                except UserIsBlockedError:
                    # some dumbass block the bot even before it sends files
                    status_for_db = "completed_but_blocked"
                    logger.error(f"User has blocked the bot! Failed to forward cached messages for pack {sticker_set.set.id,} to user {item.user_id}.")
                except Exception as e:
                    logger.error(f"Failed to forward newly cached pack {sticker_set.set.id} to user {item.user_id}: {e}")
                    await self.client.send_message(item.chat_id, "<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> An error occurred while sending your files. Please use <b>/contact</b> to report this.", parse_mode='html')
                    status_for_db = "failed_forward"

        else: # caching is off or cache channels full or its a custom premium request
            if not is_silent_mode:
                await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='6080182302397174299'>✅</tg-emoji> Conversion complete! Sending <b>{len(wastickers_files)}</b> {'file' if len(wastickers_files) == 1 else 'files'}...", link_preview=False, parse_mode='html')
                
                all_uploads_succeeded = True
                try:
                    await self.network_task.upload_files(wastickers_files, pack_url, safe_pack_title, item.chat_id)
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
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Timed out while uploading pack. Please try again later.", parse_mode="html")
                    else:
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Timed out while uploading pack part {first_failed_index+1}. Please try again later.", parse_mode="html")
                    #notify owner
                    user = await self.client.get_entity(item.user_id)
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "UploadTimeout", f"File: {', '.join(failed_uploads)}", sticker_set=sticker_set)
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
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Failed to upload pack due to an error. Please use <b>/contact</b> to report it to the admins.", parse_mode="html")
                    else:
                        await self.client.send_message(item.chat_id, f"<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Failed to upload pack part {first_failed_index+1} due to an error. Please use <b>/contact</b> to report it to the admins.", parse_mode="html")
                    #notify owner
                    user = await self.client.get_entity(item.user_id)
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, "UploadError", f"File: {', '.join(failed_uploads)}", sticker_set=sticker_set)
                finally:
                    # This ensures temporary .wastickers files are deleted if they weren't cached and moved.
                    for file_path in wastickers_files:
                        if os.path.exists(file_path):
                            logger.debug(f"Cleaning up temporary output file: {file_path}")
                            os.remove(file_path)

                # If all uploads were successful
                if all_uploads_succeeded:
                    await self.client.send_message(item.chat_id, "<tg-emoji emoji-id='5872922883092648417'>📱</tg-emoji> To import to WhatsApp, use an app like '<b>Sticker Maker</b>' on your phone (/help for more info). Enjoy!", parse_mode="html")
                    
        return status_for_db

    async def process_queue(self):
        """Process the conversion queue."""
        async with self.processing_lock:
            while True and not self.shutting_down:
                item = await queue_manager.get_next_item()
                if not item:
                    break

                # ------ last cache check (if applicable) ------------------
                # check for cache suspecious item (for user items)
                if item.is_cache_suspicious:
                    logger.info(f"Re-checking cache for suspicious item from user {item.user_id} (Log ID: {item.log_id})")
                    try:
                        # We pass the item's log_id so we don't create a new DB entry
                        if await self.check_cache(item.chat_id, item.user_id, item.message_id, item.sticker_set_info, log_id=item.log_id):
                            logger.info(f"Suspicious item was a cache hit! Skipping conversion.")
                            await self.client.edit_message(entity=item.chat_id, message=item.bot_reply_message_id, text=f"<tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> The pack you requested was processed instantly from the cache.", parse_mode="html")
                            await queue_manager.complete_processing(item.id, success=True)
                            continue # Success! Move to the next item in the queue.
                    except Exception as e:
                        logger.error(f"Error during suspicious cache check for log {item.log_id}: {e}")
                
                # Before running cache refresh or add, check if the pack got cached by another process (for system items)
                if item.is_silent_mode:
                    sticker_set_info = item.sticker_set_info
                    try:
                        cache_status, channel_id, msg_ids = await db.is_pack_cached(sticker_set_info['set_id'], sticker_set_info['title'], len(sticker_set_info['doc_info']))

                        if cache_status == 'hit':
                            # The DB says it's cached. Let's quickly verify the files are still there.
                            messages = await self.client.get_messages(channel_id, ids=msg_ids)

                            if messages and all(m is not None for m in messages):
                                await db.record_cache_hit(sticker_set_info['set_id'], is_system_process=True) # update last upadted timestamp 

                                # The cache is valid and exists. We can safely skip this redundant job.
                                logger.info(f"Skipping processing for pack '{sticker_set_info['short_name']}' (Log ID: {item.log_id}) as it's already cached.")
                                
                                # We must properly close out this queue item and log it.
                                await db.update_conversion_log(item.log_id, "completed_skipped_pre_cached", datetime.now(timezone.utc), 0.0)
                                await queue_manager.complete_processing(item.id, success=True)
                                
                                # And importantly clean up our system job trackers i mean those damn sets
                                if item.log_id in self.active_refresh_jobs:
                                    self.active_refresh_jobs.discard(item.log_id)
                                    if not self.active_refresh_jobs and not self.active_refresh_message:
                                        await self.client.send_message(OWNER_ID, "<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Cache refresh operation complete!", parse_mode="html")
                                
                                if item.log_id in self.active_add_jobs:
                                    self.active_add_jobs.discard(item.log_id)
                                    if not self.active_add_jobs and not self.active_add_message:
                                        await self.client.send_message(OWNER_ID, "<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Add-to-cache operation complete!", parse_mode="html")

                                continue # Success! Move to the next item in the queue.
                                
                    except Exception as e:
                        logger.warning(f"Pre-check failed for pack '{sticker_set_info['short_name']}': {e}. Proceeding with conversion as a fallback.")


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
                    user = await self.client.get_entity(item.user_id)
                    user_display_name =get_user_display_name(user)
                    await self.notification_manager.send_conversion_failure(item.user_id, user_display_name, item.log_id, f"Some error that you never expeted: {type(e).__name__}", str(e), sticker_set_info=item.sticker_set_info)

                finally:
                    # Update the database log
                    completion_time = datetime.now(timezone.utc)
                    duration = (completion_time - start_time).total_seconds()
                    await db.update_conversion_log(item.log_id, status_for_db, completion_time, duration)
                    if status_for_db.startswith("completed"):
                        success = True
                    await queue_manager.complete_processing(item.id, success)

                    # check if it was a system generated task ---------      
      
                    if item.log_id in self.active_refresh_jobs:
                        self.active_refresh_jobs.discard(item.log_id)
                        # If that was the last job, notify the owner
                        if not self.active_refresh_jobs:
                            logger.info("All cache refresh jobs have been completed.")
                            await self.client.send_message(OWNER_ID, "<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Cache refresh operation complete!", parse_mode="html")

                    if item.log_id in self.active_add_jobs:
                        self.active_add_jobs.discard(item.log_id)
                        # If that was the last job, notify the owner
                        if not self.active_add_jobs and not self.active_add_message:
                            logger.info("All add-cache jobs have been completed.")
                            await self.client.send_message(OWNER_ID, "<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Add-to-cache operation complete!", parse_mode="html")


    async def _get_user_from_event(self, event: events.NewMessage.Event, arg: Optional[str]) -> Optional[object]:
        """Helper to get user from command argument or reply."""
        entity = None
        if event.reply_to_msg_id and not arg:
            reply_msg = await event.get_reply_message()
            entity = await reply_msg.get_sender()
        elif arg:
            try:
                # Check if it's a numeric ID first
                if arg.isdigit():
                    entity = await self.client.get_entity(int(arg))
                else: # Assume it's a username
                    entity = await self.client.get_entity(arg)
            except Exception:
                await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Invalid user ID or username.", parse_mode="html")
                return None
        if entity is None:
            await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Could not find the user.", parse_mode="html")
            return None
        elif not isinstance(entity, User) or entity.bot:
            await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> This is not a valid user.", parse_mode="html")
            return None
        return entity

    # ------- User commands -----------

    @check_banned
    async def start_command(self, event: events.NewMessage.Event):
        """Handle /start command."""
        user = await event.get_sender()
        # Log user on /start
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        await db.add_or_update_user(user.id, user.username, full_name)
        
        # Check for deep linking arguments
        args = event.raw_text.split()
        if len(args) > 1:
            parameter = args[1].lower().strip()
            
            # Dispatch to appropriate handlers based on the parameter
            if parameter == 'queue':
                return await self.queue_command(event)
            elif parameter == 'mystats':
                return await self.mystats_command(event)
            elif parameter == 'premium':
                return await self.premium_command(event)
            elif parameter == 'commands':
                return await self.commands_command(event)
            elif parameter == 'contact':
                return await self.contact_command(event)
            elif parameter == 'suggest':
                return await self.suggest_command(event)
            elif parameter == 'help':
                return await self.help_command(event)
        
        await event.reply(self.START_MESSAGE, buttons=self.START_BUTTONS, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def help_command(self, event: events.NewMessage.Event):
        """Handle /help command."""
        buttons = [
            [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Commands", b"commands", style = "success", icon=5787544344906959608)]
        ]
        await event.reply(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def mystats_command(self, event: events.NewMessage.Event):
        """Displays the user's current status and conversion stats."""
        user = await event.get_sender()
        is_premium = await db.is_premium(user.id)
        
        # user role
        role = "Regular User <tg-emoji emoji-id='5316727448644103237'>👤</tg-emoji>"
        if db.is_owner(user.id):
            role = "Owner <tg-emoji emoji-id='5433758796289685818'>👑</tg-emoji>"
        elif await db.is_admin(user.id):
            role = "Admin <tg-emoji emoji-id='5854973145315806460'>👮</tg-emoji>"
        elif is_premium:
            role = "Premium User <tg-emoji emoji-id='5967522716062847679'>⭐</tg-emoji>"
            duration_left = await db.get_premium_duration_left(user.id)
            if duration_left:
                days = duration_left.days
                hours = duration_left.seconds // 3600
                minutes = (duration_left.seconds % 3600) // 60
                role += f"\n<b>Expires in</b>: {days}d {hours}h {minutes}m <tg-emoji emoji-id='5258258882022612173'>⏳</tg-emoji>"
            
        # get conversion stats
        stats = await db.get_user_stats(user.id)
        daily_usage = await db.get_daily_usage(user.id)
        daily_limit = DAILY_LIMIT_PREMIUM if is_premium else DAILY_LIMIT_REGULAR
        limit_str = f"{daily_usage}/{daily_limit}" if daily_limit > 0 else "Unlimited"
        
        message = (
            f"<tg-emoji emoji-id='5431577498364158238'>📊</tg-emoji> <b>Your Stats</b>\n\n"
            f"<b>Status:</b> {role}\n"
            f"<b>Today's Usage:</b> {limit_str}\n\n"
            f"<b>Conversions Log:</b>\n"
            f"  • Total Requests: {stats['total']}\n"
            f"    <tg-emoji emoji-id='6296367896398399651'>✅</tg-emoji> Succeeded: {stats['succeeded']}\n"
            f"    <tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> Failed: {stats['failed']}\n"
            f"    <tg-emoji emoji-id='5319112319429523945'>🚫</tg-emoji> Cancelled: {stats['cancelled']}"
        )
        
        await event.reply(message, parse_mode='html')
        logger.info(f"User {user.id} has fetched their stats.")
        raise StopPropagation

    async def _get_premium_message_text(self, user_id: int) -> str:
        """Generates the dynamic premium status message for a user."""
        # Base message with premium benefits
        benefits_message = (
            f"<b>Premium Benefits Include:</b>\n\n"
            f"<blockquote><tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji> <b>Priority Queue:</b> Your requests jump to the front of the line.</blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5449683594425410231'>📈</tg-emoji> <b>Higher Daily Limit:</b> Convert up to <b>{DAILY_LIMIT_PREMIUM}</b> packs per day (vs. {DAILY_LIMIT_REGULAR} for regular users).</blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5451882707875276247'>⚙️</tg-emoji> <b>Concurrent Conversions:</b> Convert up to {MAX_CONCURRENT_PREMIUM_REQUESTS} packs at once.</blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5370951118698339120'>✍️</tg-emoji> <b>Custom Pack Details:</b> Set your own custom title and author name for your packs.</blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5443038326535759644'>💬</tg-emoji> <b>Priority Support:</b> Get faster help in the support group.</blockquote>\n"
        )

        if await db.is_premium(user_id):
            duration_left = await db.get_premium_duration_left(user_id)
            days = duration_left.days
            hours = duration_left.seconds // 3600
            
            status_message = (
                f"<tg-emoji emoji-id='5967522716062847679'>⭐</tg-emoji> <b>You have an active Premium subscription!</b>\n"
                f"<i>Expires in: {days} days and {hours} hours.</i>\n\n"
            )
        
        else:
            status_message = (
                f"<tg-emoji emoji-id='5472125180799098428'>😕</tg-emoji> <b>You are not a Premium user.</b>\n\n"
                f"<b>Upgrade to unlock great features!</b>\n\n"
                f"<b>Pricing:</b>\n"
                f"  <tg-emoji emoji-id='5409048419211682843'>💵</tg-emoji> <b>${PREMIUM_PRICE_MONTHLY}</b> / month\n"
                f"  <tg-emoji emoji-id='5224257782013769471'>💰</tg-emoji> <b>${PREMIUM_PRICE_YEARLY}</b> / year (<i>Save over {PREMIUM_SAVINGS_PERCENT}%</i>)\n\n"
                f"<tg-emoji emoji-id='5337239271851960809'>✉️</tg-emoji>Contact an admin at <b>{SUPPORT_GROUP}</b> to get started.\n\n"
            )
        
        return status_message + benefits_message


    @check_banned
    async def premium_command(self, event: events.NewMessage.Event):
        """Displays the user's premium status and benefits."""
        user = await event.get_sender()
        
        
        message_text = await self._get_premium_message_text(user.id)
        buttons = [
            [Button.url("Contact Admin", SUPPORT_GROUP_LINK, style="success", icon=5895457880710058528)],
            [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Help", b"help", style = "primary", icon=5818947586702184246)]
        ]

        await event.reply(message_text, buttons=buttons, parse_mode='html', link_preview=False)
        raise StopPropagation

    @check_banned
    async def queue_command(self, event: events.NewMessage.Event):
        """Command to check user's position."""
        user = await event.get_sender()
        position = await queue_manager.get_queue_position(user.id)
        stats = await queue_manager.get_queue_stats()
        total = stats["total_waiting"] + (1 if stats["currently_processing"] else 0)

        if position == -1: # user is processing
            message = "<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji> Your pack is currently being processed! It should be ready soon."
            buttons = [[Button.inline("Refresh", b"check_queue", style = "primary", icon=5260687119092817530)]]
        elif position > 0: # in queue
            message = QUEUE_CHECK_MESSAGE.format(
                position=position,
                total=total
            )
            buttons = [[Button.inline("Refresh", b"check_queue", style = "primary", icon=5260687119092817530)]]
        else: # not in the queue
            message = f"<tg-emoji emoji-id='5305381957524272531'>📊</tg-emoji> You're not in the queue. Total in queue: {total}."
            buttons = [
                [Button.inline("Refresh", b"check_queue", style = "success", icon=5260687119092817530)],
                [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909)]
            ]
        
        await event.reply(message, buttons=buttons, parse_mode='html')
        raise StopPropagation
    
    @check_banned
    async def commands_command(self, event: events.NewMessage.Event):
        """Handles the /commands command."""
        buttons = [
            [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Help", b"help", style = "success", icon=5818947586702184246)]
        ]
        await event.reply(COMMANDS_MESSAGE, buttons=buttons, parse_mode='html')
        raise StopPropagation
    

    def _format_suggestion_message(self, list_type: str) -> tuple[str, list]:
        """Helper to generate the message text and buttons for suggestions."""
        packs = self.daily_popular_packs if list_type == 'daily' else self.all_time_popular_packs
        
        if list_type == 'daily':
            title = "<tg-emoji emoji-id='5251537301154062376'>📅</tg-emoji> <b>Top 10 Popular Packs (Daily)</b>"
            button = [Button.inline("View All-Time Top 50", b"suggest_all_time", style="primary", icon=5789828777882162072)]
        else: # all_time
            title = "<tg-emoji emoji-id='5789828777882162072'>🏆</tg-emoji> <b>Top 50 Popular Packs (All-Time)</b>"
            button = [Button.inline("View Daily Top 10", b"suggest_daily", style="primary", icon=5251537301154062376)]
        
        if not packs:
            message = f"{title}\n\n" \
                      "Hmm, I don't have any data for this yet.\n " \
                      "Check back tomorrow after more packs have been converted! <tg-emoji emoji-id='5240426590126490125'>😊</tg-emoji>"
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

        session = await session_manager.create(
            user_id=user.id,
            flow=Flow.CONTACT,
            state="awaiting_confirmation",
            ttl_seconds=3600, # Session expires in 1 hour
            single_active=True
        )

        buttons = [
            [Button.inline("Send Message", f"contact_send_{session.session_id}", style="success", icon=5253742260054409879), 
            Button.inline("Cancel", f"contact_cancel_{session.session_id}", style="danger", icon=5465665476971471368)],
            [Button.url("Support Group", SUPPORT_GROUP_LINK, style="primary", icon=5443038326535759644)]
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
            previous_replies = await db.get_previous_replies(contact_id)

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
                    await db.log_admin_reply(contact_id, admin_id, sent_msg.id, reply_content)
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
        stats = await db.get_gstats()
        q_stats = await queue_manager.get_queue_stats()

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
            f"  • Currently Processing: {processing_user}"
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
            target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
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
            # Clean up the temporary dump file
            if dump_path and os.path.exists(dump_path):
                os.remove(dump_path)
        
        raise StopPropagation

    async def id_command(self, event: events.NewMessage.Event):
        """Owner command to get IDs of custom emojis sent in the message."""
        if not getattr(event.message, 'entities', None):
            await event.reply("No custom emojis found in the message.")
            raise StopPropagation

        emoji_list = []
        
        for entity, item_text in event.message.get_entities_text():
            if isinstance(entity, MessageEntityCustomEmoji):
                emoji_list.append(f"<tg-emoji emoji-id='{entity.document_id}'>{item_text}</tg-emoji>  :  <code>{entity.document_id}</code>")
        
        if not emoji_list:
            await event.reply("<tg-emoji emoji-id='5852812849780362931'>❌️</tg-emoji> No custom emojis found in the message.", parse_mode='html')
            raise StopPropagation
            
        await event.reply("\n".join(emoji_list), parse_mode='html')
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
                    await event.reply(f"Error: Logs file upload failed.\n**Error**: {e}")
                    return
            except Exception as e:
                logger.error(f"An error occured: {e}")
                await event.reply(f"Error: An error occured: {e}")
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path) # Clean up the zip file

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
        original_event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id, 'bot_reply_message_id': 0}
        self.pending_actions[action_id] = { "action_type": action_type, "payload": action_payload, "original_event_info": original_event_info }
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
                await db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
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

    async def _execute_refresh_task(self, action_type: str, payload: dict, original_event_info: dict):
        """The background task that fetches pack details and queues them for refresh."""
        system_id = SYSTEM_USER_ID
        packs_to_queue = []
        
        if action_type == "refreshcache_top_n":
            limit = payload['limit']
            if self.active_refresh_message: await self.client.edit_message(self.active_refresh_message, f"Step 1/2: Clearing entire cache...")
            
            # Clear entire cache
            all_packs = await db.get_all_cached_pack_ids()
            asyncio.create_task(self.delete_multiple_cache(all_packs))

            all_packs_short_name = None
            if limit == "all":
                all_packs_short_name = await db.get_all_known_pack_short_names()
            packs_to_queue = all_packs_short_name if limit == "all" else await db.get_top_packs_by_score(limit) 

        elif action_type == "refreshcache_links":
            pack_names = payload['pack_short_names']
            if self.active_refresh_message: await self.client.edit_message(self.active_refresh_message, f"Step 1/2: Clearing cache for {len(pack_names)} specified packs...")
            # Clear specified packs and prepare for queueing
            set_ids = []
            for name in pack_names:
                set_id = await db.get_set_id_by_short_name(name)
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

                is_emoji = sticker_set.set.emojis
                pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{short_name}"
                log_id = await db.log_conversion_request(system_id, sticker_set.set.id, pack_url, is_emoji)

                sticker_set_doc_mime_type = [doc.mime_type for doc in sticker_set.documents]
                sticker_set_info = {"set_id": sticker_set.set.id, "access_hash": sticker_set.set.access_hash, "short_name": sticker_set.set.short_name, "is_emoji": sticker_set.set.emojis, "doc_info": sticker_set_doc_mime_type, "title": sticker_set.set.title, }
                estimated_seconds = estimate_wait_time(sticker_set_info['doc_info'], None)

                await queue_manager.add_to_queue(
                    user_id=system_id, chat_id=original_event_info['chat_id'], message_id=original_event_info['message_id'], username="System Refresh", bot_reply_message_id=original_event_info['bot_reply_message_id'],
                    sticker_set_info=sticker_set_info, estimated_seconds=estimated_seconds, log_id=log_id,
                    priority=SYSTEM_PRIORITY, is_cache_suspicious=False,
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
            queue_stats = await queue_manager.get_queue_stats()
            is_processing = queue_stats["currently_processing"]
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

        original_event_info = {'user_id': event.sender_id, 'chat_id': event.chat_id, 'message_id': event.message.id, 'bot_reply_message_id': 0}
        self.pending_actions[action_id] = {"action_type": action_type, "payload": action_payload, "original_event_info": original_event_info}
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

        if not self.active_add_jobs and not active_session:
            await event.reply("✅ No active add-cache operation to cancel.")
            return
        
        # Handle cancelling the interactive mode
        if active_session:
            await session_manager.expire(user_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ Interactive add-cache mode has been cancelled.")

        if not self.active_add_jobs:
            return # No background jobs to cancel

        msg = await event.reply(f"Cancelling {len(self.active_add_jobs)} queued add-cache jobs...")

        cancelled_count = 0
        jobs_to_cancel = list(self.active_add_jobs)
        for log_id in jobs_to_cancel:
            if await queue_manager.cancel_item(user_id=SYSTEM_USER_ID, log_id=log_id):
                await db.update_conversion_log(log_id, "cancelled_by_admin", datetime.now(timezone.utc), 0.0)
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
        user_id = event.sender_id
        active_session = await self._get_active_add_cache_session(user_id)
        if active_session:
            await session_manager.expire(user_id, Flow.ADDCACHE, active_session.session_id)
            await event.reply("✅ **Finished!** Exited interactive add-cache mode.")
        else:
            await event.reply("✅ You are not in an active interactive mode.")
        # Silently ignore if not in the correct state
        raise StopPropagation

    async def _execute_addcache_task(self, action_type: str, payload: dict, original_event_info: dict):
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
                packs_to_process = await db.get_non_cached_packs()

            elif action_type == "addcache_n":
                limit = payload['limit']
                if self.active_add_message: await self.client.edit_message(self.active_add_message, f"Step 1/2: Fetching top {limit} non-cached packs from the database...")
                packs_to_process = await db.get_non_cached_packs(limit=limit)
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

                cache_status, channel_id, message_ids = await db.is_pack_cached(set_id, set_title, set_count)
                
                if cache_status == 'hit':
                    try:
                        messages = await self.client.get_messages(channel_id, ids=message_ids)
                        if messages and all(msg is not None for msg in messages):
                            await db.record_cache_hit(set_id, is_system_process=True)
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

                is_emoji = sticker_set.set.emojis
                pack_url = f"https://t.me/add{'emoji' if is_emoji else 'stickers'}/{short_name}"
                log_id = await db.log_conversion_request(system_id, sticker_set.set.id, pack_url, is_emoji)
                
                sticker_set_doc_mime_type = [doc.mime_type for doc in sticker_set.documents]
                sticker_set_info = {"set_id": sticker_set.set.id, "access_hash": sticker_set.set.access_hash, "short_name": sticker_set.set.short_name, "is_emoji": sticker_set.set.emojis, "doc_info": sticker_set_doc_mime_type, "title": sticker_set.set.title, }
                estimated_seconds = estimate_wait_time(sticker_set_info['doc_info'], None)
                
                await queue_manager.add_to_queue(
                    user_id=system_id, chat_id=original_event_info['chat_id'], message_id=original_event_info['message_id'], username="System AddCache", bot_reply_message_id=original_event_info['bot_reply_message_id'],
                    sticker_set_info=sticker_set_info, estimated_seconds=estimated_seconds, log_id=log_id,
                    priority=SYSTEM_PRIORITY, is_cache_suspicious=False,
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
            queue_stats = await queue_manager.get_queue_stats()
            is_processing = queue_stats["currently_processing"]
            if not is_processing:
                asyncio.create_task(self.process_queue())

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
            if os.path.exists(file_path):
                os.remove(file_path)
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
        self.pending_actions[action_id] = {"action_type": "clearjunk"}

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


    # ------------- Admins commands ---------------

    async def add_premium_command(self, event: events.NewMessage.Event):
        """Admin command to add a premium user."""
        if not await db.is_admin(event.sender_id):
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
        
        if await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user is already premium. Use `/extendpremium` to extend their duration.")
            raise StopPropagation
            
        try:
            success = await db.add_premium(target_user.id, target_user.username, duration_days, event.sender_id)
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except Exception as e:
            logger.error(f"An error has occurred while adding {target_user.id} to premium by {event.sender_id}. Error: {e}")
            await event.reply("❌ An error has occurred maybe this is not a valid user or the user hasn't started the bot.")
            raise StopPropagation
        if success:
            expiry = datetime.now(timezone.utc) + timedelta(days=duration_days)
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(
                f"⭐ Successfully granted premium to **{full_name}** (`{target_user.id}`)!\n"
                f"Expires in: `{duration_days}` days (on `{expiry.strftime('%Y-%m-%d %H:%M')} UTC`)."
            )
            logger.info(f"User {target_user.id} granted {duration_days} days of premium by admin: {event.sender_id}")
        else:
            await event.reply("❌ Failed to add user to premium. Maybe they are already premium.")
        raise StopPropagation
    
    async def remove_premium_command(self, event: events.NewMessage.Event):
        """Admin command to remove a premium user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/removepremium <user_id/@username>` or reply to a user.")
            raise StopPropagation
        
        if not await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user does not have an active premium subscription.")
            raise StopPropagation

        if await db.remove_premium(target_user.id, event.sender_id):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"✅ Premium status for **{full_name}** (`{target_user.id}`) has been revoked.")
            logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
        else:
            await event.reply("❌ An error occurred. Could not remove premium status.")
        raise StopPropagation

    async def extend_premium_command(self, event: events.NewMessage.Event):
        """Admin command to extend a premium user's subscription."""
        if not await db.is_admin(event.sender_id):
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

        if not await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user isn't premium. Use `/addpremium` to grant them premium first.")
            raise StopPropagation
        
        days_to_add = int(days_arg)
        try:
            new_expiry = await db.manage_premium_duration(target_user.id, days_to_add, event.sender_id, 'extended')
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
        if not await db.is_admin(event.sender_id):
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

        if not await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user does not have an active premium subscription.")
            raise StopPropagation
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        current_days_left= await db.get_premium_duration_left(target_user.id).days
        if int(days_arg) > current_days_left:
            if await db.remove_premium(target_user.id, event.sender_id):
                await event.reply(f"✅ Since **{full_name}** had only `{current_days_left}` days of premium left, they have been **removed** from premium.")
                logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
            else:
                await event.reply("❌ An error occurred. Could not remove premium status.")
            raise StopPropagation

        days_to_deduct = -abs(int(days_arg)) # Ensure it's a negative number
        try:
            new_expiry = await db.manage_premium_duration(target_user.id, days_to_deduct, event.sender_id, 'deducted')
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
        if not await db.is_admin(event.sender_id):
            raise StopPropagation # Only admins can use this

        target_user = await self._get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/getstats <user_id/@username>` or reply to a user's message.")
            raise StopPropagation
        
        if await db.is_user(target_user.id):
            is_premium = await db.is_premium(target_user.id)
            
            # Get user role for display
            role = "👤 Regular User"
            if db.is_owner(target_user.id):
                role = "👑 Owner"
            elif await db.is_admin(target_user.id):
                role = "👮‍♂️ Admin"
            elif is_premium:
                role = "⭐ Premium User"
                duration_left = await db.get_premium_duration_left(target_user.id)
                if duration_left:
                    days = duration_left.days
                    hours = duration_left.seconds // 3600
                    minutes = (duration_left.seconds % 3600) // 60
                    role += f"\n⏳ **Expires in**: {days}d {hours}h {minutes}m"
            
            stats = await db.get_user_stats(target_user.id)
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            daily_usage = await db.get_daily_usage(target_user.id)
            daily_limit = DAILY_LIMIT_PREMIUM if is_premium else DAILY_LIMIT_REGULAR
            limit_str = f"{daily_usage}/{daily_limit}" if daily_limit > 0 else "Unlimited"
            
            message = (
                f"📊 **Stats for [{full_name}](tg://user?id={target_user.id})** (`{target_user.id}`)\n\n"
                f"**Status**: {role}\n"
                f"**Today's Usage**: `{limit_str}`\n\n"
                f"**Conversions Log**:\n"
                f"  • Total Requests: `{stats['total']}`\n"
                f"  • ✅ Succeeded: `{stats['succeeded']}`\n"
                f"  • ❌ Failed: `{stats['failed']}`\n"
                f"  • 🚫 Cancelled: `{stats['cancelled']}`"
            )
        else:
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            message = f"🫤 The user **[{full_name}](tg://user?id={target_user.id})** has not started the bot yet."
        
        if await db.is_banned(target_user.id):
            message += "\n\n🚫 **This user has been banned.**"
        
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
        if not await db.is_admin(event.sender_id):
            raise StopPropagation
        
        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/sban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or await db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation

        if await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is already banned.")
            raise StopPropagation
        
        if await db.ban_user(target_user.id, event.sender_id, reason, is_silent=True):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"🚫 **Silently Banned {full_name}** (`{target_user.id}`).")
            logger.info(f"User {target_user.id} silently banned by admin: {event.sender_id}. Reason: {reason}")
        else:
            await event.reply(f"❌ Failed to ban `{target_user.id}`. Maybe they are already banned.")
        raise StopPropagation
    
    # notified ban command 
    async def ban_command(self, event: events.NewMessage.Event):
        """Admin command to ban a user and NOTIFY them."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/ban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or await db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation
        
        if await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is already banned.")
            raise StopPropagation

        if await db.ban_user(target_user.id, event.sender_id, reason, is_silent=False):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            logger.info(f"User {target_user.id} banned by admin: {event.sender_id}. Reason: {reason}")
            
            notification_status = ""
            try:
                await self.client.send_message(
                    target_user.id,
                    f"🚫 **You have been banned** from using this bot by an administrator.\n\n**Reason:** {reason}"
                )
                notification_status = "User has been notified."
            except Exception as e:
                logger.warning(f"Could not notify user {target_user.id} about their ban: {e}")
                notification_status = "Could not notify the user (they may have blocked the bot or haven't started yet)."

            await event.reply(f"🚫 **Banned {full_name}** (`{target_user.id}`).\n{notification_status}")
        else:
            await event.reply("❌ Failed to ban user. User might have already been banned.")
        raise StopPropagation

    # unban command
    async def unban_command(self, event: events.NewMessage.Event):
        """Admin command to unban a user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)
            
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/unban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if not await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is not currently banned.")
            raise StopPropagation

        if await db.unban_user(target_user.id, event.sender_id, reason):
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
                if await self.check_user_membership(user_id):
                    await event.answer("✅ Great! You're now a member.")
                    await event.edit("<tg-emoji emoji-id='5208541126583136130'>✅</tg-emoji> Great! You're now a member.\n\n" + self.START_MESSAGE, buttons=self.START_BUTTONS, link_preview=False, parse_mode='html')
                else:
                    try:
                        await event.answer("❌ You still need to join the required channels.")
                        await event.edit("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> You still need to join the required channels.\n\n" + CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
                    except Exception as e:
                        logger.warning(f"Could not edit the Join message: {e}")
            
            elif data.startswith("cancel_item_"):
                await event.answer()
                log_id = int(data.split("_", 2)[2])
                success = await queue_manager.cancel_item(user_id, log_id)
                if success:
                    await db.update_conversion_log(log_id, "cancelled", datetime.now(timezone.utc), 0.0)
                    await db.decrement_daily_requests(user_id)
                    await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Your request has been successfully cancelled.", parse_mode="html")
                else:
                    await event.edit("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Could not cancel. The item may be processing or completed.", parse_mode="html")

            elif data == "check_queue":
                position = await queue_manager.get_queue_position(user_id)
                stats = await queue_manager.get_queue_stats()
                total = stats["total_waiting"] + (1 if stats["currently_processing"] else 0)

                if position == -1: # user is processing
                    message = "<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji> Your pack is currently being processed! It should be ready soon."
                    buttons = [[Button.inline("Refresh", b"check_queue", style = "primary", icon=5260687119092817530)]]
                elif position > 0: # in queue
                    message = QUEUE_CHECK_MESSAGE.format(
                        position=position,
                        total=total
                    )
                    buttons = [[Button.inline("Refresh", b"check_queue", style = "primary", icon=5260687119092817530)]]
                else: # not in the queue
                    message = f"<tg-emoji emoji-id='5305381957524272531'>📊</tg-emoji> You're not in the queue. Total in queue: {total}."
                    buttons = [
                        [Button.inline("Refresh", b"check_queue", style = "success", icon=5260687119092817530)],
                        [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909)]
                    ]
                try:
                    await event.answer("Refreshed!")
                    await event.edit(message, buttons=buttons,parse_mode='html')
                except Exception as e:
                    logger.debug(f"Could not edit the check_queue message: {e}")
            
            elif data == "help":
                await event.answer()
                buttons = [
                    [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Commands", b"commands", style = "success", icon=5787544344906959608)]
                ]
                await event.edit(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

            elif data == "start":
                await event.answer()
                await event.edit(self.START_MESSAGE, buttons=self.START_BUTTONS, link_preview=False, parse_mode='html')
            
            elif data == "premium":
                await event.answer()
                message_text = await self._get_premium_message_text(user_id)
                buttons = [
                    [Button.url("Contact Admin", SUPPORT_GROUP_LINK, style = "success", icon=5895457880710058528)],
                    [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Help", b"help", style = "primary", icon=5818947586702184246)]
                ]
                await event.edit(message_text, buttons=buttons, parse_mode='html', link_preview=False)

            elif data == "commands":
                await event.answer()
                buttons = [
                    [Button.inline("Back to Start", b"start", style = "primary", icon=5258236805890710909), Button.inline("Help", b"help", style = "success", icon=5818947586702184246)]
                ]
                await event.edit(COMMANDS_MESSAGE, buttons=buttons, parse_mode='html')

            elif data == "contact_cancel_reply":
                await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Action cancelled. The reply was not sent.", parse_mode="html")
                
            elif data.startswith("contact_send_"):
                await event.answer()
                *_, sid = data.split("_", 2)
                session = await session_manager.get(user_id, Flow.CONTACT, sid)

                if session and session.active:
                    # Update the session state to wait for the user's message
                    await session_manager.update(user_id, Flow.CONTACT, sid, state="awaiting_contact_message", ttl_seconds=3600)
                    prompt_message = await event.edit(
                        "<tg-emoji emoji-id='5319161050128459957'>✅</tg-emoji> Great! Please send the message you'd like to forward now. You can reply to this message or send a new one.", 
                        buttons=[Button.inline("Cancel", f"contact_cancel_{sid}", style="danger", icon=5465665476971471368)], parse_mode="html"
                    )
                    # Link this message to the session so we can identify replies
                    await session_manager.mark_message(user_id, Flow.CONTACT, sid, event.chat_id, prompt_message.id)
                else:
                    await event.edit("This action has expired. Please use /contact again.")

            elif data.startswith("contact_cancel_"):
                await event.answer()
                *_, sid = data.split("_", 2)
                session = await session_manager.get(user_id, Flow.CONTACT, sid)

                if session and session.active:
                    # Expire the session to deactivate it
                    await session_manager.expire(user_id, Flow.CONTACT, sid)
                    await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Action cancelled.", buttons=None, parse_mode="html")
                else:
                    await event.edit("This action has expired or already completed.")

            elif data.startswith("contact_force_reply_"):
                await event.answer()
                try:
                    *_, contact_id_str, admin_msg_id_str = data.split("_")
                    contact_id = int(contact_id_str)
                    admin_msg_id = int(admin_msg_id_str)

                    # Fetch details of the original user
                    details = await db.get_contact_details(contact_id)
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
                    await db.log_admin_reply(contact_id, user_id, sent_msg.id, reply_content)
                    logger.info(f"An admin replied to the already replied user {original_user_id}")
                    await event.edit("✅ Your additional reply has been sent.")
                except Exception as e:
                    logger.error(f"Failed to send duplicate admin reply to {original_user_id}: {e}")
                    await event.edit(f"❌ An error occurred: {e}")

            elif data.startswith("contact_details_"):
                await event.answer()
                *_, contact_id_str, admin_msg_id_str = data.split("_")
                contact_id = int(contact_id_str)
                
                details = await db.get_contact_details(contact_id)
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
                previous_replies = await db.get_previous_replies(int(contact_id_str))
                
                prompt_text = f"⚠️ **This query has already been handled {len(previous_replies)} time(s).**\n\nAre you sure you want to send another reply?"
                buttons = [
                    [Button.inline("✔️ Yes, reply again", f"contact_force_reply_{contact_id_str}_{admin_msg_id_str}")],
                    [Button.inline("❌ Cancel", "contact_cancel_reply")],
                    [Button.inline("🔍 Show Reply Details", f"contact_details_{contact_id_str}_{admin_msg_id_str}")]  
                ]
                await event.edit(prompt_text, buttons=buttons)

            elif data.startswith("cancel_session_"):
                try:
                    *_, flow_val, sid = data.split("_", 3)
                    flow = Flow(flow_val)
                    session = await session_manager.get(user_id, flow, sid)
                    session_active = session and session.active
                        
                    msg = await event.get_message()
                    text = telethon_html.unparse(msg.message, msg.entities)
                    text_to_remove = None
                    buttons = msg.buttons

                    for row in buttons:
                        for btn in row:
                            if btn.data and btn.data.decode() == data:
                                row.remove(btn)
                                text_to_remove = btn.text.replace("Cancel: ", "")
                                break
                        if not row:
                            buttons.remove(row)
                    
                    if session_active:
                        await session_manager.expire(user_id, flow, sid)
                        await event.answer(f"✅ Cancelled {text_to_remove}")
                        # if only clear all button is there but all actions have been already cleared individually
                        if len(buttons)==1:
                            await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> All pending actions have been cancelled.", parse_mode='html')
                        else:
                            final_text = text.replace(html.escape(text_to_remove), "<s>Cancelled</s>", 1)
                            await event.edit(text=final_text, buttons=buttons, parse_mode='html')
                    else:
                        await event.answer("⛔ This action has already expired or been cancelled.")
                        # if only clear all button is there but all actions have been already cleared individually
                        if len(buttons)==1:
                            await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> All pending actions have been cancelled.", parse_mode='html')
                        else:
                            final_text = text.replace(html.escape(text_to_remove), "<s>Already expired/cancelled</s>", 1)
                            await event.edit(text=final_text, buttons=buttons, parse_mode='html')

                except Exception as e:
                    logger.error(f"Error cancelling session from callback: {e}")
                    await event.answer("❌ Could not cancel this action.", alert=True)

            elif data == "cancel_all_input_sessions":
                await event.answer("🧹 Cancelling all pending inputs...")
                active_sessions_with_flow = await self._get_active_input_sessions(user_id)
                
                if not active_sessions_with_flow:
                    await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> No pending actions to cancel.", parse_mode='html')
                    return
                    
                cancelled_count = 0
                for session, flow in active_sessions_with_flow:
                    await session_manager.expire(user_id, flow, session.session_id)
                    cancelled_count += 1
                    
                await event.edit(f"<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Cancelled {cancelled_count} pending {'action' if cancelled_count==1 else 'actions'}.", parse_mode='html')


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
                    users = await db.get_gstats_premium_list()
                    content = ""
                    for user in users:
                        expiry = user['expiry_date'].strftime('%Y-%m-%d %H:%M')
                        content += f"• <code>{user['user_id']}</code> (@{user['username'] or 'N/A'}) - Expires: <code>{expiry}</code>\n"
                    await self._gstats_send_list(event, "Active Premium Members", content, "premium_users.txt")

                elif action == "top_users":
                    users = await db.get_gstats_top_users()
                    content = ""
                    for i, user in enumerate(users, 1):
                        content += f"{i}. <code>{user['user_id']}</code> ({html.escape(user['full_name'])}) - <b>{user['total_requests']}</b> requests\n"
                    await self._gstats_send_list(event, "Top 50 Users by Requests", content, "top_users.txt")

                elif action == "admins":
                    users = await db.get_gstats_admins_list()
                    content = ""
                    for user in users:
                        content += f"• <code>{user['user_id']}</code> (@{user['username'] or 'N/A'})\n"
                    await self._gstats_send_list(event, "Admins List", content, "admins.txt")

                elif action == "banned":
                    users = await db.get_gstats_banned_list()
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
                    await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅</tg-emoji> Action cancelled.", parse_mode="html")
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
                        await db.log_broadcast(user_id, message_for_db, flags_for_db, len(target_ids), success_count, fail_count, is_forward, fwd_chat_id, fwd_msg_id)
                    elif action_type == 'send':
                        await db.log_send(user_id, message_for_db, flags_for_db, target_ids, success_count, fail_count, is_forward, fwd_chat_id, fwd_msg_id)

                    await event.edit(
                        f"✅ **{action_type.capitalize()} Complete!**\n\n"
                        f"• Sent to: `{success_count}` users\n"
                        f"• Failed for: `{fail_count}` users"
                    )
                    return
                
                elif action_type == 'clearcache_all':
                    packs_to_clear = pending_action['payload']['packs_to_clear']
                    await event.edit(f"🗑️ Deleting all {len(packs_to_clear)} cached packs from Telegram channels...")
                    
                    results = await self.delete_multiple_cache(packs_to_clear)
                    logger.info(f"Cache Cleared! Succeeded: {results['succeeded']}, Failed/Junked: {results['failed']}, Not Found: {results['not_found']}.")
                    await event.edit(
                        f"✅ **Cache Clear Operation Complete!**\n\n"
                        f"• Successfully deleted: `{results['succeeded']}`\n"
                        f"• Failed (logged as junk): `{results['failed']}`\n"
                        f"• Not found in DB: `{results['not_found']}`"
                    )
                    return

                elif action_type == 'clearcache_packs':
                    pack_short_names = pending_action['payload']['pack_short_names']
                    await event.edit(f"Processing {len(pack_short_names)} packs to clear from cache...")

                    success_list, fail_list, not_found_list = [], [], []

                    for name in pack_short_names:
                        set_id = await db.get_set_id_by_short_name(name)
                        if not set_id:
                            not_found_list.append(f"• `{name}` (Not in stats DB)")
                            continue
                        
                        result = await self.delete_cache(set_id)
                        if result is True:
                            success_list.append(f"• `{name}`")
                        elif result is False:
                            fail_list.append(f"• `{name}`")
                        else: # None
                            not_found_list.append(f"• `{name}` (Not in cache DB)")

                    
                    logger.info(f"Cache clear complete! Succeeded: {len(success_list)}, Failed/Junked: {len(fail_list)}, Not Found: {len(not_found_list)}")
                    response_message = "✅ **Cache Clearing Complete!**\n\n"
                    if success_list:
                        response_message += f"**Successfully Cleared:**\n" + "\n".join(success_list) + "\n\n"
                    if fail_list:
                        response_message += f"**Failed (Logged as Junk):**\n" + "\n".join(fail_list) + "\n\n"
                    if not_found_list:
                        response_message += f"**Not Found:**\n" + "\n".join(not_found_list)
                    
                    await event.edit(response_message)
                    return
                
                elif action_type in ("refreshcache_top_n", "refreshcache_links"):
                    self.active_refresh_message = await event.edit("🚀 **Starting Cache Refresh...**\nThis may take a moment to prepare.", buttons=None)
                    original_event_info = pending_action['original_event_info']
                    original_event_info['bot_reply_message_id'] = event.message_id
                    asyncio.create_task(self._execute_refresh_task(action_type, pending_action['payload'], original_event_info))
                    return

                elif action_type in ("addcache_all", "addcache_n", "addcache_links"):
                    self.active_add_message = await event.edit("🚀 **Starting Add-Cache...**\nThis may take a moment to prepare.", buttons=None)
                    original_event_info = pending_action['original_event_info']
                    original_event_info['bot_reply_message_id'] = event.message_id
                    asyncio.create_task(self._execute_addcache_task(action_type, pending_action['payload'], original_event_info))
                    return

                elif action_type == "addcache_interactive":
                    session = await session_manager.create(
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
                    await session_manager.mark_message(user_id, Flow.ADDCACHE, session.session_id, event.chat_id, event.message_id)
                    return
                
                elif action_type == 'clearjunk':
                    await event.edit("🗑️ Clearing junk file entries from the database...")
                    cleared_count = await db.clear_junk_file_entries()
                    await event.edit(f"✅ Successfully cleared **{cleared_count}** junk file entries from the database.")
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

                session = await session_manager.get(user_id, Flow.CUSTOMIZE, sid)
                if not session or not session.active:
                    await event.edit("This customization session has <b>expired</b>. Please send the sticker again.", parse_mode='html')
                    return
                
                payload = session.payload

                if action == "title":
                    await session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_custom_title', ttl_seconds=3600)
                    await event.edit(
                        "Okay, send me the new <b>title</b> for your sticker pack (max 50 characters).",
                        buttons=[[Button.inline("Back", f"customize_back_{sid}", style="primary", icon=5877629862306385808)]],
                        parse_mode='html'
                    )
                    await session_manager.mark_message(user_id, Flow.CUSTOMIZE, sid, event.chat_id, event.message_id)

                elif action == "author":
                    await session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_custom_author', ttl_seconds=3600)
                    await event.edit(
                        "Sure, send me the <b>author name</b> you'd like to use (max 30 characters).",
                        buttons=[[Button.inline("Back", f"customize_back_{sid}", style="primary", icon=5877629862306385808)]], 
                        parse_mode='html'
                    )
                    await session_manager.mark_message(user_id, Flow.CUSTOMIZE, sid, event.chat_id, event.message_id)

                elif action == "back":
                    await event.answer()
                    await session_manager.update(user_id, Flow.CUSTOMIZE, sid, state='awaiting_customization_choice', ttl_seconds=3600)
                    await self._update_customization_prompt(user_id, session)

                elif action == "cancel":
                    await session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                    await event.edit("<tg-emoji emoji-id='5336985409220001678'>✅️</tg-emoji> Conversion cancelled.", buttons=None, parse_mode='html')

                elif action == "convert":
                    # We need the original event object for the queue manager
                    asyncio.create_task(self.delete_multiple_messages(event.chat_id, payload['failed_inputs'], "Failed to delete invalid customization input messages.")) # clear any invalid input messaages

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

                    original_event_info = payload['original_event_info']
                    sticker_set_info = payload['sticker_set_info']
                    if not (payload['custom_title'] or payload['custom_author']):
                        if self.cache_enabled and await self.check_cache(original_event_info['chat_id'], original_event_info['user_id'], original_event_info['message_id'], sticker_set_info):
                            await session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                            await event.delete()
                            return
                    
                    
                    await self._queue_sticker_pack(
                        original_event_info,
                        sticker_set_info,
                        is_premium=True,
                        custom_title=payload['custom_title'],
                        custom_author=payload['custom_author']
                    )
                    await session_manager.expire(user_id, Flow.CUSTOMIZE, sid)
                    await event.delete()
