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
        self.client.add_event_handler(self.mystats_command, events.NewMessage(pattern='/mystats', func=lambda e: e.is_private))
        # admin/owner commands
        # promote/demote admin (owner only)
        self.client.add_event_handler(self.promote_command, events.NewMessage(pattern=r'/promote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.demote_command, events.NewMessage(pattern=r'/demote(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        # Premium commands (admin use)
        self.client.add_event_handler(self.add_premium_command, events.NewMessage(pattern=r'/addpremium(?:@\w+)?(?:\s+([@\w\d]+))?(?:\s+(\d+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.remove_premium_command, events.NewMessage(pattern=r'/removepremium(?:@\w+)?(?:\s+([@\w\d]+))?', func=lambda e: e.is_private))
        self.client.add_event_handler(self.extend_premium_command, events.NewMessage(pattern=r'/extendpremium(?:@\w+)?\s+([@\w\d]+)\s+(\d+)', func=lambda e: e.is_private))
        self.client.add_event_handler(self.deduct_premium_command, events.NewMessage(pattern=r'/deductpremium(?:@\w+)?\s+([@\w\d]+)\s+(\d+)', func=lambda e: e.is_private))
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
            [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("❓ Help", b"help")]
        ]
        await event.reply(self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def help_command(self, event: events.NewMessage.Event):
        """Handle /help command."""
        buttons = [
            [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("🏠 Back to Start", b"start")]
        ]
        await event.reply(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    @check_banned
    async def handle_message(self, event: events.NewMessage.Event):
        """Handle incoming messages (sticker/emoji pack URLs, stickers, or custom emojis)."""
        user = await event.get_sender()
        
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
            return

        if queue_manager.is_user_in_queue(user.id):
            position = queue_manager.get_queue_position(user.id)
            await event.reply(
                f"⏳ You're already in the queue!\n\nPosition: {position}",
                buttons=[[Button.inline("📊 Check Queue", b"check_queue")]],
                link_preview=False, parse_mode='html'
            )
            return

        pack_input = None
        pack_display_name = "Unknown Pack"
        is_emoji_pack = False
        
        if event.text:
            pack_input = extract_pack_name_from_url(event.text)
            if not pack_input:
                await event.reply(
                    "❌ **Invalid input!**\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert."
                )
                return
            if 'addemoji' in event.text:
                is_emoji_pack = True

        elif event.sticker:
            # First, get the sticker set object from the sticker attributes
            for attr in event.sticker.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    pack_input = attr.stickerset
                    # Check if the pack is for emojis
                    sticker_set = await self.converter.get_sticker_set(pack_input)
                    if sticker_set and sticker_set.set.emojis:
                        is_emoji_pack = True
                    break
            
            if not pack_input:
                await event.reply(
                    "❌ This sticker doesn't seem to belong to a pack I can access.\n\nPlease forward a sticker from a public sticker pack."
                )
                return

        elif event.document and hasattr(event.document, 'attributes'):
            # This handles custom emojis sent from the emoji panel or forwarded
            for attr in event.document.attributes:
                if isinstance(attr, DocumentAttributeCustomEmoji):
                    pack_input = attr.stickerset
                    is_emoji_pack = True
                    break
            
            if not pack_input:
                # This message is for documents that aren't recognized as emojis
                 await event.reply(
                    "❌ **Invalid input!**\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert."
                )
        # Fetch the sticker/emoji set to get its actual name
        try:
            sticker_set = await self.converter.get_sticker_set(pack_input)
            if sticker_set and sticker_set.set:
                pack_display_name = sticker_set.set.title
            else:
                # Fallback in case we can't get the name for some reason
                pack_display_name = pack_input
                logger.warning(f"Could not fetch set title for user {user.id}.")
        except Exception as e:
                logger.error(f"Error fetching set name for user {user.id}: {e}")
                pack_display_name = "the pack you sent" # Fallback on error

        user_display_name = get_user_display_name(user)

        # Log the request to the database
        log_id = db.log_conversion_request(user.id, str(pack_input), is_emoji_pack)

        position = await queue_manager.add_to_queue(
            user.id, user_display_name, event.chat_id,
            event.message.id, pack_input, log_id
        )
        
        await event.reply(
            f"<b>✅ Added to conversion queue!</b>\n\n"
            f"📦 Pack: <code>{pack_display_name}</code>\n📍 Position: {position}\n\n"
            f"<blockquote>I'll notify you when the conversion starts!</blockquote>",
            buttons=[[Button.inline("📊 Check Queue", b"check_queue")]],
            link_preview=False, parse_mode='html'
        )

        if position == 1 and not self.processing_lock.locked():
            asyncio.create_task(self.process_queue())

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

                    estimated_time = estimate_wait_time(sticker_set.documents, num_packs)

                    # Edit the original message to show the real estimate
                    await self.client.edit_message(
                        entity=item.chat_id,
                        message=status_message.id,
                        text=f"🚀 Starting conversion for your pack...\n"
                            f"🤔 Estimated time: {estimated_time}"
                    )
                    if sticker_set.set.emojis:
                        await self.client.send_message(
                            item.chat_id,
                            f"📊 Pack Details:\n• Name: `{pack_title}`\n• Total emojis: {total_stickers}\n"
                            f"• This will create {num_packs} .wastickers file(s)."
                        )
                    else:
                        await self.client.send_message(
                            item.chat_id,
                            f"📊 Pack Details:\n• Name: `{pack_title}`\n• Total stickers: {total_stickers}\n"
                            f"• This will create {num_packs} .wastickers file(s)."
                        )
                    wastickers_files = await self.converter.create_wastickers_pack(sticker_set, item.username)
                    
                    if wastickers_files:
                        await self.client.send_message(item.chat_id, f"✅ Conversion complete! Sending {len(wastickers_files)} file(s)...")
                        for i, file_path in enumerate(wastickers_files):
                            caption = f"📦 {os.path.basename(file_path)} - Part {i+1}/{len(wastickers_files)}\nSize: {format_file_size(os.path.getsize(file_path))}"
                            await self.client.send_file(item.chat_id, file_path, caption=caption)
                            os.remove(file_path)
                        success = True
                        status_for_db = "completed"
                        
                        await self.client.send_message(item.chat_id, "📱 To import to WhatsApp, use an app like '**Sticker Maker**' on your phone (/help for more info). Enjoy!")
                        success = True
                    else:
                        await self.client.send_message(item.chat_id, f"❌ Failed to convert the pack '{pack_title}'. There might have been an issue with the sticker files themselves.")
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
            
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        db.add_premium(target_user.id, target_user.username, duration_days, event.sender_id)
        expiry = datetime.now() + timedelta(days=duration_days)
        
        await event.reply(
            f"⭐ Successfully granted premium to **{full_name}** (`{target_user.id}`)!\n"
            f"Expires in: `{duration_days}` days (on {expiry.strftime('%Y-%m-%d')})."
        )
        logger.info(f"User {target_user.id} granted {duration_days} days of premium by {event.sender_id}")
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
        new_expiry = db.manage_premium_duration(target_user.id, days_to_add, event.sender_id, 'extended')
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        await event.reply(
            f"✅ Extended premium for **{full_name}** by `{days_to_add}` days.\n"
            f"New expiry date: `{new_expiry.strftime('%Y-%m-%d')}`."
        )
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

        days_to_deduct = -abs(int(days_arg)) # Ensure it's a negative number
        new_expiry = db.manage_premium_duration(target_user.id, days_to_deduct, event.sender_id, 'deducted')
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        
        expiry_message = f"New expiry date: `{new_expiry.strftime('%Y-%m-%d')}`."
        if new_expiry < datetime.now():
            expiry_message = "Their subscription has now expired."

        await event.reply(
            f"✅ Deducted `{abs(days_to_deduct)}` days from **{full_name}**'s premium.\n{expiry_message}"
        )
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
        
        await event.reply(message)
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
        logger.info(f"User {target_user.id} silently banned by {event.sender_id}. Reason: {reason}")
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

        db.ban_user(target_user.id, event.sender_id, reason, is_silent=False)
        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        logger.info(f"User {target_user.id} banned by {event.sender_id}. Reason: {reason}")
        
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
            logger.info(f"User {target_user.id} unbanned by {event.sender_id}. Reason: {reason}")
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
                buttons = [[Button.inline("📊 Check Queue", b"check_queue"), Button.inline("❓ Help", b"help")]]
                await event.edit("✅ Great! You're now a member.\n\n" + self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
            else:
                await event.edit("❌ You still need to join the required channels.\n\n" + CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons(), link_preview=False, parse_mode='html')
        
        elif data == "check_queue":
            position = queue_manager.get_queue_position(user.id)
            stats = queue_manager.get_queue_stats()
            if position:
                message = QUEUE_CHECK_MESSAGE.format(
                    position=position,
                    total=stats["total_waiting"] + (1 if stats["currently_processing"] else 0)
                )
            else:
                message = f"📊 You're not in the queue. Total users waiting: {stats['total_waiting']}."
            
            buttons = [[Button.inline("🔄 Refresh", b"check_queue")]]
            if position is None:
                buttons.append([Button.inline("🏠 Back to Start", b"start")])
            await event.edit(message, buttons=buttons)
        
        elif data == "help":
            buttons = [
                [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("🏠 Back to Start", b"start")]
            ]
            await event.edit(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

        elif data == "start":
            buttons = [
                [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("❓ Help", b"help")]
            ]
            await event.edit(self.START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

