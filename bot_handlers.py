"""
Telegram bot handlers for the TG Sticker/Emoji to WA Sticker Converter Bot (Telethon Version)
"""

import os
import asyncio
import logging
from telethon import TelegramClient, events, Button
from telethon.errors.rpcerrorlist import UserNotParticipantError
from telethon.events import StopPropagation
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import DocumentAttributeSticker, DocumentAttributeCustomEmoji

from config import *
from utils import *
from queue_manager import queue_manager
from sticker_converter import StickerConverter

logger = logging.getLogger(__name__)

class BotHandlers:
    def __init__(self, client: TelegramClient):
        """
        Initializes the bot handlers with the Telethon client and other necessary components.
        """
        ensure_directories()
        self.client = client
        self.converter = StickerConverter(self.client)
        self.processing_lock = asyncio.Lock()

    def register_handlers(self):
        """
        Registers all event handlers with the Telethon client.
        """
        self.client.add_event_handler(self.start_command, events.NewMessage(pattern='/start', func=lambda e: e.is_private))
        self.client.add_event_handler(self.help_command, events.NewMessage(pattern='/help', func=lambda e: e.is_private))
        self.client.add_event_handler(self.handle_message, events.NewMessage(func=lambda e: e.is_private and (e.text or e.sticker)))
        self.client.add_event_handler(self.handle_callback_query, events.CallbackQuery(func=lambda e: e.is_private))

    def _create_channel_join_buttons(self) -> list:
        """Dynamically creates the inline keyboard for joining required channels."""
        keyboard = []
        for i in range(0, len(REQUIRED_CHANNELS), 2):
            row = []
            channel1_username = REQUIRED_CHANNELS[i].replace('@', '')
            row.append(Button.url(f"Join {channel1_username}", url=f"https://t.me/{channel1_username}"))

            if i + 1 < len(REQUIRED_CHANNELS):
                channel2_username = REQUIRED_CHANNELS[i+1].replace('@', '')
                row.append(Button.url(f"Join {channel2_username}", url=f"https://t.me/{channel2_username}"))
            
            keyboard.append(row)
        
        keyboard.append([Button.inline("✅ Check Again", b"check_membership")])
        return keyboard

    async def check_user_membership(self, user_id: int) -> bool:
        """Check if user is a member of required channels."""
        if not REQUIRED_CHANNELS:
            return True
        try:
            for channel in REQUIRED_CHANNELS:
                try:
                    await self.client(GetParticipantRequest(channel=channel, participant=user_id))
                except UserNotParticipantError:
                    logger.warning(f"User {user_id} is not a participant in {channel}.")
                    return False
                except Exception as e:
                    logger.error(f"Could not check membership for user {user_id} in {channel}: {e}")
                    return False
            return True
        except Exception as e:
            logger.error(f"General error in check_user_membership for user {user_id}: {e}")
            return False

    async def start_command(self, event: events.NewMessage.Event):
        """Handle /start command."""
        user = await event.get_sender()
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons())
            return
        
        buttons = [
            [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("❓ Help", b"help")]
        ]
        await event.reply(START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    async def help_command(self, event: events.NewMessage.Event):
        """Handle /help command."""
        buttons = [
            [Button.inline("📊 Check Queue", b"check_queue"), Button.inline("🏠 Back to Start", b"start")]
        ]
        await event.reply(HELP_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')
        raise StopPropagation

    async def handle_message(self, event: events.NewMessage.Event):
        """Handle incoming messages (sticker/emoji pack URLs, stickers, or custom emojis)."""
        user = await event.get_sender()
        
        if not await self.check_user_membership(user.id):
            await event.reply(CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons())
            return

        if queue_manager.is_user_in_queue(user.id):
            position = queue_manager.get_queue_position(user.id)
            await event.reply(
                f"⏳ You're already in the queue!\n\nPosition: {position}",
                buttons=[[Button.inline("📊 Check Queue", b"check_queue")]]
            )
            return

        pack_input = None
        pack_display_name = "Unknown Pack"
        
        if event.text:
            pack_input = extract_pack_name_from_url(event.text)
            if not pack_input:
                await event.reply(
                    "❌ **Invalid input!**\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert."
                )
                return
        elif event.sticker:
            # First, get the sticker set object from the sticker attributes
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
            # This handles custom emojis sent from the emoji panel or forwarded
            for attr in event.document.attributes:
                if isinstance(attr, DocumentAttributeCustomEmoji):
                    pack_input = attr.stickerset
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
        position = await queue_manager.add_to_queue(
            user.id, user_display_name, event.chat_id,
            event.message.id, pack_input
        )
        
        await event.reply(
            f"✅ Added to conversion queue!\n\n"
            f"📦 Pack: {pack_display_name}\n📍 Position: {position}\n\n"
            f"I'll notify you when the conversion starts!",
            buttons=[[Button.inline("📊 Check Queue", b"check_queue")]]
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

                success = False 
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
                        
                        await self.client.send_message(item.chat_id, "📱 To import to WhatsApp, use an app like 'Sticker Maker' on your phone. Enjoy!")
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
                    await queue_manager.complete_processing(item.user_id, success)                


    async def handle_callback_query(self, event: events.CallbackQuery.Event):
        """Handle callback queries from inline keyboards."""
        user = await event.get_sender()
        data = event.data.decode('utf-8')

        await event.answer()

        if data == "check_membership":
            if await self.check_user_membership(user.id):
                buttons = [[Button.inline("📊 Check Queue", b"check_queue"), Button.inline("❓ Help", b"help")]]
                await event.edit("✅ Great! You're now a member.\n\n" + START_MESSAGE, buttons=buttons)
            else:
                await event.edit("❌ You still need to join the required channels.\n\n" + CHANNEL_JOIN_MESSAGE, buttons=self._create_channel_join_buttons())
        
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
            await event.edit(START_MESSAGE, buttons=buttons, link_preview=False, parse_mode='html')

