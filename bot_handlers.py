"""
Telegram bot handlers for the TG Sticker/Emoji to WA Sticker Converter Bot (Telethon Version)
"""

import os
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
        # admin/owner commands
        # promote/demote admin (owner only)
        self.client.add_event_handler(self.promote_command, events.NewMessage(pattern=r'/promote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.demote_command, events.NewMessage(pattern=r'/demote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
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
            row.append(Button.url(f"Join {name1}", url=link1))

            # Second Button in Row (if it exists)
            if i + 1 < len(REQUIRED_CHANNELS_FORMATTED):
                name2, link2 = REQUIRED_CHANNELS_FORMATTED[i+1][:2]
                row.append(Button.url(f"Join {name2}", url=link2))
            
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


    async def process_queue(self):
        """Process the conversion queue."""
        async with self.processing_lock:
            while True:
                item = await queue_manager.get_next_item()
                if not item:
                    break

                try: # edit the queue message
                    await self.client.edit_message(
                        entity=item.chat_id,
                        message=item.bot_reply_message_id,
                        text=f"⌛ Your request for the pack is now processing...",
                        buttons=None
                    )
                except Exception as e:
                    logger.warning(f"Could not edit message {item.bot_reply_message_id} to remove cancel button: {e}")

                start_time = datetime.now()
                success = False 
                status_for_db = "failed"
                try:
                    status_message = await self.client.send_message(
                        item.chat_id, 
                        "🚀 Starting conversion for your pack...\n"
                        "🤔 Estimated time: `Calculating...`"
                    )
                    
                    sticker_set = await self.converter.get_sticker_set(item.pack_input)
                    if not sticker_set:
                        error_pack_name = item.pack_input if isinstance(item.pack_input, str) else "the pack you sent"
                        await self.client.send_message(item.chat_id, f"❌ Failed to find pack: `{error_pack_name}`. It might be private or invalid.")
                        # success is still false
                        continue

                    pack_title = sticker_set.set.title
                    total_stickers = len(sticker_set.documents)
                    num_packs = (total_stickers + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
                    pack_short_name = sticker_set.set.short_name
                    is_emoji_pack = sticker_set.set.emojis

                    estimated_time = estimate_wait_time(sticker_set.documents, num_packs)

                    # Edit the original message to show the real estimate
                    await self.client.edit_message(
                        entity=item.chat_id,
                        message=status_message.id,
                        text=f"🚀 Starting conversion for your pack...\n"
                            f"🤔 Estimated time: {estimated_time}"
                    )


                    if is_emoji_pack:
                        pack_type_url = "addemoji"
                        item_name = "emojis"
                    else:
                        pack_type_url = "addstickers"
                        item_name = "stickers"
                        
                    pack_url = f"https://t.me/{pack_type_url}/{pack_short_name}"

                    # Construct the message
                    message = (f"📊 <b>Pack Details:</b>\n"
                            f"• Name: <a href=\"{pack_url}\">{pack_title}</a>\n"
                            f"• Total {item_name}: {total_stickers}\n"
                            f"• This will create {num_packs} .wastickers file(s).")
                    
                    await self.client.send_message(item.chat_id, message, parse_mode='html', link_preview=False)
                    
                    wastickers_files = await self.converter.create_wastickers_pack(sticker_set, item.username)
                    
                    if wastickers_files:
                        await self.client.send_message(item.chat_id, f"✅ Conversion complete for <b><a href=\"{pack_url}\">{pack_title}</a></b>! Sending <b>{len(wastickers_files)}</b> file(s)...", link_preview=False, parse_mode='html')
                        for i, file_path in enumerate(wastickers_files):
                            caption = f"📦 <a href=\"{pack_url}\">{pack_title}</a> - Part {i+1}/{len(wastickers_files)}\nSize: {format_file_size(os.path.getsize(file_path))}"
                            await self.client.send_file(item.chat_id, file_path, caption=caption, link_preview=False, parse_mode='html')
                            os.remove(file_path)
                        success = True
                        status_for_db = "completed"
                        
                        await self.client.send_message(item.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")
                        success = True
                    else:
                        await self.client.send_message(item.chat_id, f"❌ Failed to convert the pack <b><a href=\"{pack_url}\">{pack_title}</a></b>. There might have been an issue with the sticker files themselves.", link_preview=False, parse_mode='html')
                        # success is still false

                except Exception as e:
                    logger.error(f"Error processing queue item for user {item.user_id}: {e}", exc_info=True)
                    try:
                        await self.client.send_message(item.chat_id, "❌ An unexpected error occurred during conversion. The developers have been notified. Please try again later.")
                    except: pass
                    # success is still false

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
            if position is None:
                buttons.append([Button.inline("🏠 Back to Start", b"start")])
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