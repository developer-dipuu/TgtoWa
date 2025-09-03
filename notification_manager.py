import html
import logging
import traceback
from telethon import TelegramClient
from config import NOTIFICATION_GROUP_ID, ADMINS_TO_MENTION, NOTIFICATIONS

logger = logging.getLogger(__name__)

class NotificationManager:
    """A dedicated class for handling and sending bot notifications."""
    def __init__(self, client: TelegramClient):
        self.client = client
        self.group_id = NOTIFICATION_GROUP_ID
        self.admins_to_mention = ADMINS_TO_MENTION
        self.settings = NOTIFICATIONS

    async def _send_notification(self, notification_type: str, message: str):
        """Internal helper to format and send a notification based on config."""
        config = self.settings.get(notification_type)
        if not self.group_id or not config or not config.get("enabled"):
            return

        mention_str = ""
        if config.get("mention_admins") and self.admins_to_mention:
            # Create invisible mentions that still ping
            mention_links = [f"<a href=\"tg://user?id={uid}\">\u200b</a>" for uid in self.admins_to_mention]
            mention_str = "".join(mention_links)

        # Append mentions to the end of the message
        final_message = f"{message}{mention_str}"

        try:
            await self.client.send_message(
                self.group_id,
                final_message,
                parse_mode='html',
                link_preview=False
            )
        except Exception as e:
            logger.error(f"CRITICAL: FAILED TO SEND NOTIFICATION. Type: {notification_type}. Error: {e}")

    async def send_conversion_failure(self, user_id, user_display_name, log_id, error_type, error_message, sticker_set = None, sticker_set_info = None):
        """Sends a notification for a failed sticker conversion."""
        pack_url = "N/A"
        if sticker_set and sticker_set.set:
            pack_type = "addemoji" if sticker_set.set.emojis else "addstickers"
            pack_url = f"https://t.me/{pack_type}/{sticker_set.set.short_name}"
        elif sticker_set_info:
            pack_type = "addemoji" if sticker_set_info['is_emoji'] else "addstickers"
            pack_url = f"https://t.me/{pack_type}/{sticker_set_info['short_name']}"
        safe_user_name = html.escape(user_display_name)
        safe_error_msg = html.escape(str(error_message))

        message = (
            f"🚨 <b><u>Conversion Failure</u></b> 🚨\n\n"
            f"A conversion has failed. Details below:\n\n"
            f"👤 <b>User:</b> {safe_user_name} (<code>{user_id}</code>)\n"
            f"📦 <b>Pack URL:</b> <a href=\"{pack_url}\">Click Here</a>\n"
            f"📄 <b>Log ID:</b> <code>{log_id}</code>\n"
            f"🛑 <b>Error Type:</b> <code>{error_type}</code>\n"
            f"🗒️ <b>Error Details:</b>\n"
            f"<pre><code>{safe_error_msg}</code></pre>"
        )
        await self._send_notification("conversion_failure", message)

    async def send_uncaught_exception(self, exc_info):
        """Sends a notification for an unhandled exception in the event loop."""
        exc_type, exc_value, exc_tb = exc_info
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        
        # Keep it under Telegram's message limit
        if len(tb_str) > 3500:
            tb_str = tb_str[:1750] + "\n...\n" + tb_str[-1750:]

        safe_tb = html.escape(tb_str)
        
        message = (
            f"💥 <b><u>CRITICAL: Uncaught Exception</u></b> 💥\n\n"
            f"An unhandled exception occurred in a background task. The bot might be in an unstable state.\n\n"
            f"<b>Traceback:</b>\n<pre><code>{safe_tb}</code></pre>"
        )
        await self._send_notification("uncaught_exception", message)
    
    async def send_cache_delete_failure(self, channel_id, message_ids, error):
        """Sends a notification for a failure in deleting cache files."""
        message = (
            f"⚠️ <b><u>Cache Deletion Failure</u></b> ⚠️\n\n"
            f"Failed to delete cache messages. Manual intervention may be required.\n\n"
            f"<b>Channel ID:</b> <code>{channel_id}</code>\n"
            f"<b>Message IDs:</b> <code>{message_ids}</code>\n"
            f"<b>Error:</b>\n<code>{html.escape(str(error))}</code>"
        )
        await self._send_notification("cache_delete_failure", message)

    async def send_message_delete_failure(self, chat_id, message_ids, custom_log, error):
        """Sends a notification for a generic message deletion failure."""
        message = (
            f"📄 <b><u>Message Deletion Failure</u></b>\n\n"
            f"Failed to delete one or more messages.\n\n"
            f"<b>Context:</b> {html.escape(custom_log)}\n"
            f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"<b>Message IDs:</b> <code>{message_ids}</code>\n"
            f"<b>Error:</b>\n<code>{html.escape(str(error))}</code>"
        )
        await self._send_notification("message_delete_failure", message)