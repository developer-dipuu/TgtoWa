"""
Telegram bot handlers for the TG Sticker/Emoji to WA Sticker Converter Bot (Telethon Version)
"""

import os 
import glob
import zipfile
import asyncio
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, events, Button
from telethon.errors.rpcerrorlist import UserNotParticipantError
from telethon.events import StopPropagation
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import DocumentAttributeSticker, DocumentAttributeCustomEmoji, Message

from config import *
from utils import *
from queue_manager import queue_manager
from sticker_converter import StickerConverter
import database as db

logger = logging.getLogger(__name__)

class BotHandlers:
    def __init__(self, client: TelegramClient, bot_info):
        """
        Initializes the bot handlers with the Telethon client and other necessary components.
        """
        ensure_directories()
        self.client = client
        self.converter = StickerConverter(self.client)
        self.processing_lock = asyncio.Lock()
        self.bot_username = f"@{bot_info.username}"
        # formatted start message
        self.START_MESSAGE = START_MESSAGE_FORMAT.format(
            bot_username=self.bot_username,
        )
        

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
        
        # owner commands
        self.client.add_event_handler(self.promote_command, events.NewMessage(pattern=r'/promote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.demote_command, events.NewMessage(pattern=r'/demote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.broadcast_command, events.NewMessage(pattern=r'/broadcast', func=lambda e: db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.gstats_command, events.NewMessage(pattern=r'/gstats', func=lambda e: db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.getdb_command, events.NewMessage(pattern='/getdb', func=lambda e: db.is_owner(e.sender_id)))
        self.client.add_event_handler(self.getlogs_command, events.NewMessage(pattern='/getlogs', func=lambda e: db.is_owner(e.sender_id)))
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
        self.client.add_event_handler(self.handle_message, events.NewMessage(func=lambda e: e.is_private and not e.text.startswith('/') and (e.text or e.sticker)))
        self.client.add_event_handler(self.handle_callback_query, events.CallbackQuery(func=lambda e: e.is_private))

    
    def _create_channel_join_buttons(self) -> list:
        """Dynamically creates buttons for both public and private channels."""
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


    @check_banned
    async def handle_message(self, event: events.NewMessage.Event):
        """Handle incoming messages (sticker/emoji pack URLs, stickers, or custom emojis)."""
        user = await event.get_sender()
        
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
            return

        is_premium = db.is_premium(user.id)
        current_queue_count = await queue_manager.get_user_queue_count(user.id)
        limit = MAX_CONCURRENT_PREMIUM_REQUESTS if is_premium else MAX_CONCURRENT_REGULAR_REQUESTS

        # max queue limit
        if current_queue_count >= limit:
            if is_premium:
                message = (f"⏳ **You've reached your limit!**\n\n"
                        f"You currently have {current_queue_count}/{limit} items in the queue. "
                        f"Please wait for one to complete before adding more.")
            else:
                message = "⏳ You're already in the queue! Please wait for your current request to complete."

            await event.reply(message, buttons=[[Button.inline("📊 Check Queue", b"check_queue")]])
            return
        
        # now time to extract pack details based on the type of message sent
        pack_input = None
        pack_display_name = "Unknown Pack"
        is_emoji_pack = False
        
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
            sticker_set = await self.converter.get_sticker_set(pack_input)

            if not sticker_set or not sticker_set.documents:
                 logger.error(f"Could not fetch a valid sticker set for input: {pack_input}")
                 await event.reply("❌ I couldn't find that sticker pack. It might be private, invalid, or empty. Please try another one!")
                 return
            # find estimated time
            estimated_seconds = estimate_wait_time(sticker_set.documents, None)
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
            if sticker_set and sticker_set.set:
                pack_display_name = sticker_set.set.title
            else:
                # Fallback in case we can't get the name for some reason
                pack_display_name = pack_input
                logger.warning(f"Could not fetch set title for user {user.id}.")
        except Exception as e:
                logger.error(f"Error fetching set name for user {user.id}: {e}")
                pack_display_name = "the pack you sent" # Fallback on error

        # get the user's name pack url
        user_display_name = get_user_display_name(user)
        pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
        pack_url = f"https://t.me/{pack_type_url}/{sticker_set.set.short_name}"

        # Log the request to the database
        log_id = db.log_conversion_request(user.id, pack_url, is_emoji_pack)

        # send adding to queue message
        placeholder_message = await event.reply("⌛ Adding to the queue...")

        # add to queue and get position for this item
        position = await queue_manager.add_to_queue(
                user_id=user.id,
                username=user_display_name,
                chat_id=event.chat_id,
                message_id=event.message.id,
                bot_reply_message_id=placeholder_message.id,
                pack_input=pack_input,
                sticker_set=sticker_set,
                estimated_seconds=estimated_seconds,
                log_id=log_id,
                is_premium=is_premium
        )
        # detailed added to queue successful message string
        if position != 1:
            if is_premium:
                current_queue_count = await queue_manager.get_user_queue_count(user.id)
                slots_left = MAX_CONCURRENT_PREMIUM_REQUESTS - current_queue_count
                final_message_text = (f"<b>⭐ VIP Status Confirmed!</b>\n\n"
            f"Your pack: <b><a href=\"{pack_url}\">{pack_display_name}</a></b> has been fast-tracked to position <b>{position}</b>.\n")

                if slots_left > 0:
                    final_message_text += f"<blockquote>As a premium user, you can still add <b>{slots_left}</b> more pack(s) to the queue. Keep 'em coming!</blockquote>\n"

                final_message_text += "\n<b>I'll notify you when the conversion starts!</b>"
            else:
                final_message_text = (f"<b>✅ Added to conversion queue!</b>\n\n"
                f"📦 Pack: <a href=\"{pack_url}\">{pack_display_name}</a>\n📍 Position: {position}\n\n"
                f"<blockquote>I'll notify you when the conversion starts!</blockquote>")

            # finally edit the message with detailed one
            await self.client.edit_message(
                entity=placeholder_message.chat_id,
                message=placeholder_message.id,
                text=final_message_text,
                buttons=[[Button.inline("📊 Check Queue", b"check_queue")],[Button.inline("❌ Cancel", data=f"cancel_{log_id}".encode())]],
                link_preview=False, parse_mode='html'
            )

        if not self.processing_lock.locked():
            # Check if anyone is processing before starting a new process_queue task
            is_processing = queue_manager.get_queue_stats()["currently_processing"]
            if not is_processing:
                asyncio.create_task(self.process_queue())


    async def _notify_owner_of_failure(self, item, error_type: str, error_message: str):
        """Sends a detailed failure notification to the bot owner."""
        try:
            user_display_name = item.username
            user_id = item.user_id
            log_id = item.log_id
            
            # Reconstruct the pack URL for easy checking
            pack_url = "N/A"
            if item.sticker_set and item.sticker_set.set:
                is_emoji = item.sticker_set.set.emojis
                pack_type = "addemoji" if is_emoji else "addstickers"
                pack_url = f"https://t.me/{pack_type}/{item.sticker_set.set.short_name}"

            message = (
                f"🚨 **Conversion Failure Notification** 🚨\n\n"
                f"A conversion has failed. Here are the details:\n\n"
                f"👤 **User:** {user_display_name} (`{user_id}`)\n"
                f"📦 **Pack URL:** {pack_url}\n"
                f"📄 **Log ID:** `{log_id}`\n"
                f"🛑 **Error Type:** `{error_type}`\n"
                f"🗒️ **Error Details:**\n"
                f"```\n{error_message}\n```\n\n"
                f"The failure has been logged to the database."
            )

            await self.client.send_message(OWNER_ID, message, link_preview=False)
            logger.info(f"Sent failure notification to owner for log_id {log_id}")

        except Exception as e:
            logger.error(f"CRITICAL: Failed to send failure notification to owner for log_id {log_id}: {e}")


    async def _run_conversion(self, item):
        """
        The core logic for converting a single sticker pack.
        Raises an Exception on any failure to signal the caller.
        """
        try:
            await self.client.edit_message(
                entity=item.chat_id,
                message=item.bot_reply_message_id,
                text=f"⌛ Your request for the pack is now processing...",
                buttons=None
            )
        except Exception as e:
            logger.warning(f"Could not edit message {item.bot_reply_message_id} to remove cancel button: {e}")

        status_message = await self.client.send_message(
            item.chat_id,
            "🚀 Starting conversion for your pack...\n"
            "🤔 Estimated time: `Calculating...`"
        )
        # sticker info
        sticker_set = item.sticker_set
        pack_title = sticker_set.set.title
        total_stickers = len(sticker_set.documents)
        num_packs = (total_stickers + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
        pack_short_name = sticker_set.set.short_name
        is_emoji_pack = sticker_set.set.emojis
        estimated_seconds = item.estimated_seconds
        processing_timeout = max(60, estimated_seconds * 2)

        # Round off the time for better UI
        if estimated_seconds < 60:
            estimated_time_str = f"{round(estimated_seconds)} seconds"
        else:
            minutes = round(estimated_seconds / 60)
            estimated_time_str =  f"~{minutes} minute(s)"

        await self.client.edit_message(
            entity=item.chat_id,
            message=status_message.id,
            text=f"🚀 Starting conversion for your pack...\n"
                f"🤔 Estimated time: {estimated_time_str}"
        )
        
        pack_type_url = "addemoji" if is_emoji_pack else "addstickers"
        item_name = "emojis" if is_emoji_pack else "stickers"
        pack_url = f"https://t.me/{pack_type_url}/{pack_short_name}"

        message = (f"📊 <b>Pack Details:</b>\n"
                f"• Name: <a href=\"{pack_url}\">{pack_title}</a>\n"
                f"• Total {item_name}: {total_stickers}\n"
                f"• This will create {num_packs} .wastickers file(s).")
        await self.client.send_message(item.chat_id, message, parse_mode='html', link_preview=False)

        # Run the conversion with a timeout (either 60 sec or 2x the estimated time)
        try:
            wastickers_files = await asyncio.wait_for(self.converter.create_wastickers_pack(sticker_set, item.username), timeout=processing_timeout)
        except asyncio.TimeoutError:
            logger.error(f"Conversion timed out while creating .wasticker files.")
            try:
                await self.client.send_message(
                    item.chat_id,
                    (f"⏱️ The conversion for your pack took longer than expected and has timed out.❌\n"
                    f"Please try again later or with a different pack.\n\n"
                    f"If the problem persists, ping us at **{SUPPORT_GROUP}**")
                )
            except Exception as e:
                logger.warning(f"Could not send timeout message to {item.user_id}: {e}")
            raise asyncio.TimeoutError("Time out while creating .wasticker files.")

        if not wastickers_files:
            await self.client.send_message(item.chat_id, f"❌ Failed to convert the pack <b><a href=\"{pack_url}\">{pack_title}</a></b>. If the problem persists, ping us at **{SUPPORT_GROUP}**", link_preview=False, parse_mode='html')
            # This is a failure, so we raise an exception.
            raise Exception("Wasticker file creation returned no files.")

        # If we get here, conversion was successful, now we upload.
        await self.client.send_message(item.chat_id, f"✅ Conversion complete! Sending <b>{len(wastickers_files)}</b> file(s)...", link_preview=False, parse_mode='html')
        
        for i, file_path in enumerate(wastickers_files):
            caption = f"📦 <a href=\"{pack_url}\">{pack_title}</a> - Part {i+1}/{len(wastickers_files)}\nSize: {format_file_size(os.path.getsize(file_path))}"
            try:
                await asyncio.wait_for(
                    self.client.send_file(item.chat_id, file_path, caption=caption, link_preview=False, parse_mode='html'),
                    timeout=UPLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error(f"Upload timeout for user {item.user_id}, file {file_path}")
                if num_packs == 1:
                    await self.client.send_message(item.chat_id, f"❌ Timed out while uploading pack. Please try again later.")
                else:
                    await self.client.send_message(item.chat_id, f"❌ Timed out while uploading pack part {i+1}. Please try aagain later.")
                raise asyncio.TimeoutError("Time out while uploading the pack.")
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
            


        # If all uploads were successful
        await self.client.send_message(item.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")


    async def process_queue(self):
        """Process the conversion queue."""
        async with self.processing_lock:
            while True:
                item = await queue_manager.get_next_item()
                if not item:
                    break

                start_time = datetime.now()
                success = False 
                status_for_db = "failed"
                try:                    
                    await self._run_conversion(item)

                    # If the above line completes without any exception, it was a success.
                    success = True
                    status_for_db = "completed"

                except asyncio.TimeoutError as e:
                    status_for_db = "failed_timeout"
                    logger.error(f"Processing timed out for user {item.user_id}. ERROR: {e}")
                    await self._notify_owner_of_failure(item, "TimeoutError", str(e))

                except Exception as e: # other generic exceptions
                    status_for_db = "failed_exception"
                    logger.error(f"An exception occurred while processing queue item for user {item.user_id}: {e}")
                    await self._notify_owner_of_failure(item, type(e).__name__, str(e))

                finally:
                    # Update the database log
                    completion_time = datetime.now()
                    duration = (completion_time - start_time).total_seconds()
                    db.update_conversion_log(item.log_id, status_for_db, completion_time, duration)
                    await queue_manager.complete_processing(item.user_id, success)               

    # Admin commands starts here
    async def _get_user_from_event(self, event: events.NewMessage.Event, arg: Optional[str]) -> Optional[Message.sender]:
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
            except (ValueError, TypeError):
                await event.reply("❌ Invalid user ID or username.")
                return None
        return None

    # user commands
    @check_banned
    async def start_command(self, event: events.NewMessage.Event):
        """Handle /start command."""
        user = await event.get_sender()
        # Log user on /start
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        db.add_or_update_user(user.id, user.username, full_name)

        # if not await self.check_user_membership(user.id):
        #     await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
        #     return
        
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
            f"  • ❌ Failed: `{stats['failed']}`"
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

    # owner's command
    async def broadcast_command(self, event: events.NewMessage.Event):
        """Owner command to broadcast a message to all users."""
        
        message_to_broadcast = None
        text_to_broadcast = None
        
        # Check for -noforward flag first
        no_forward = '-noforward' in event.text.lower()
        
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
                # Scenario 3: Extract text content from the command message
                # This will strip '/broadcast' and '-noforward' to get only the message
                command_parts = event.text.split()
                
                # Find where the actual message content begins
                content_index = 1 # Start after '/broadcast'
                if len(command_parts) > 1 and command_parts[1].lower() == '-noforward':
                    content_index = 2 # Start after '/broadcast -noforward'
                
                if len(command_parts) > content_index:
                    text_to_broadcast = " ".join(command_parts[content_index:])

        # If no content could be determined, show usage instructions
        if not message_to_broadcast and not text_to_broadcast:
            await event.reply("ℹ️ **Usage:** Reply to a message with `/broadcast` or send `/broadcast <your message>`.\n\nAdd `-noforward` when replying to send as a copy instead of forwarding.")
            return

        user_ids = db.get_all_user_ids() # [cite: 1]
        total_users = len(user_ids)
        
        status_msg = await event.reply(f"🚀 Starting broadcast to {total_users} users...")
        
        success_count = 0
        fail_count = 0
        
        for user_id in user_ids:
            try:
                if text_to_broadcast:
                    # If we only have text, we must send it as a new message.
                    await self.client.send_message(user_id, text_to_broadcast, link_preview=False)
                elif no_forward:
                    # If -noforward is used, send a copy of the replied/forwarded message
                    await self.client.send_message(user_id, message_to_broadcast)
                else:
                    # Otherwise, forward the replied/forwarded message
                    await self.client.forward_messages(user_id, message_to_broadcast)
                
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.warning(f"Failed to broadcast to user {user_id}: {e}")
            
            # A short delay to avoid hitting Telegram's API rate limits
            await asyncio.sleep(0.1)

        await status_msg.edit(
            f"✅ **Broadcast Complete!**\n\n"
            f"• Sent to: `{success_count}` users\n"
            f"• Failed for: `{fail_count}` users"
        )
        raise StopPropagation

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
            f"  • ❌ Failed: `{stats['total_failed']}`\n\n"
            f"📈 **Conversions (Today):**\n"
            f"  • ✅ Succeeded: `{stats['today_succeeded']}`\n"
            f"  • ❌ Failed: `{stats['today_failed']}`\n\n"
            f"⏳ **Live Queue Status:**\n"
            f"  • Waiting: `{q_stats['total_waiting']}`\n"
            f"  • Currently Processing: `{processing_user}`"
        )

        buttons = [
            [Button.inline("⭐ Premium Members", b"gstats_premium"), Button.inline("🏆 Top 50 Users", b"gstats_top_users")],
            [Button.inline("👮‍♂️ Admins List", b"gstats_admins"), Button.inline("🚫 Banned List", b"gstats_banned")]
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

        raise StopPropagation

    # owner's command
    async def demote_command(self, event: events.NewMessage.Event):
        """Owner command to demote an admin."""
        if not db.is_owner(event.sender_id):
            return

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
                logger.error(f"An error occured: {e}")
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path) # Clean up the zip file

        else: # Send the latest (current) log
            logger.info(f"Owner {event.sender_id} requested the latest log file.")
            # Find the uncompressed .log file (logrotate leaves today's log uncompressed)
            # Your .screenrc names it based on session and window, e.g., tgBot-0.log
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
                else:
                    logger.error(f"Warning: No .log files found in {log_dir}")
                    await event.reply("🤔 No `.log` file found. Seems something's wrong.")
            except Exception as e:
                logger.error(f"An error occured while getting logs: {e}")
        raise StopPropagation
    
    # Admins commands
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
            
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        try:
            db.add_premium(target_user.id, target_user.username, duration_days, event.sender_id)
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except Exception as e:
            await event.reply("❌ An unknown error has occurred; please contact the developer")
            raise StopPropagation
        
        expiry = datetime.now() + timedelta(days=duration_days)
        
        await event.reply(
            f"⭐ Successfully granted premium to **{full_name}** (`{target_user.id}`)!\n"
            f"Expires in: `{duration_days}` days (on `{expiry.strftime('%Y-%m-%d %H:%M')}`)."
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
            f"  • ❌ Failed: `{stats['failed']}`"
        )
        logger.info(f"Stats of user {target_user.id} has been fetched by admin: {event.sender_id}")
        await event.reply(message)
        raise StopPropagation
    

    # silent ban command
    async def sban_command(self, event: events.NewMessage.Event):
        """Admin command to SILENTLY ban a user."""
        if not db.is_admin(event.sender_id):
            raise StopPropagation

        # Extract the reason. The reason is everything after the user argument.
        parts = event.raw_text.split(maxsplit=2)
        user_arg = parts[1] if len(parts) > 1 else None
        reason = parts[2] if len(parts) > 2 else "No reason provided."

        target_user = await self._get_user_from_event(event, user_arg)
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

        parts = event.raw_text.split(maxsplit=2)
        user_arg = parts[1] if len(parts) > 1 else None
        reason = parts[2] if len(parts) > 2 else "No reason provided."

        target_user = await self._get_user_from_event(event, user_arg)
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

        parts = event.raw_text.split(maxsplit=2)
        user_arg = parts[1] if len(parts) > 1 else None
        reason = parts[2] if len(parts) > 2 else "No reason provided."
        
        target_user = await self._get_user_from_event(event, user_arg)
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
        user = await event.get_sender()
        data = event.data.decode('utf-8')

        await event.answer()

        if data == "check_membership":
            if await self.check_user_membership(user.id):
                buttons = [
                    [Button.inline("💎 Premium", b"premium"), Button.inline("❓ Help", b"help")],
                    [Button.url("💬 Support Group", SUPPORT_GROUP_LINK), Button.inline("🤖 Commands", b"commands")]
                ]
                await event.edit("✅ Great! You're now a member.\n\n" + self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
            else:
                await event.edit("❌ You still need to join the required channels.\n\n" + CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
        
        elif data.startswith("cancel_"):
            log_id = int(data.split("_", 1)[1])
            success = await queue_manager.cancel_item(user.id, log_id)
            if success:
                await event.edit("✅ Your request has been successfully cancelled.")
            else:
                await event.edit("❌ Could not cancel. The item may be processing or completed.")

        elif data == "check_queue":
            position = queue_manager.get_queue_position(user.id)
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
                logger.warning(f"Could not edit the check_queue message: {e}")
        
        elif data == "help":
            buttons = [
                [Button.inline("🏠 Back to Start", b"start"), Button.inline("🤖 Commands", b"commands")]
            ]
            await event.edit(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

        elif data == "start":
            buttons = [
                [Button.inline("💎 Premium", b"premium"), Button.inline("❓ Help", b"help")],
                [Button.url("💬 Support Group", SUPPORT_GROUP_LINK), Button.inline("🤖 Commands", b"commands")]
            ]
            await event.edit(self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        
        elif data == "premium":
            message_text = await self._get_premium_message_text(user.id)
            buttons = [
                [Button.url("💬 Contact Admin", SUPPORT_GROUP_LINK)],
                [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
            ]
            await event.edit(message_text, buttons=buttons, parse_mode='html', link_preview=False)

        elif data == "commands":
            buttons = [
                [Button.inline("🏠 Back to Start", b"start"), Button.inline("❓ Help", b"help")]
            ]
            await event.edit(COMMANDS_MESSAGE, buttons=buttons, parse_mode='html')
        

        elif data.startswith("gstats_"):
            action = data.split("_", 1)[1]

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
                    content += f"{i}. <code>{user['user_id']}</code> ({user['full_name']}) - <b>{user['total_requests']}</b> requests\n"
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
                    content += f"• <code>{user['user_id']}</code> - Banned on <code>{ban_date}</code>\n  Reason: {user['reason']}\n\n"
                await self._gstats_send_list(event, "Banned Users List", content, "banned_users.txt")

            elif action == "back":
                message, buttons = await self._get_gstats_message_and_buttons()
                await event.edit(message, buttons=buttons)
