import html
import logging
import traceback
from telethon import TelegramClient
from src.core.config import NOTIFICATION_GROUP_ID, ADMINS_TO_MENTION, NOTIFICATIONS
from src.utils.formatters import get_user_display_name

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
            f"<tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji> <b><u>Conversion Failure</u></b> <tg-emoji emoji-id='5019523782004441717'>❌</tg-emoji>\n\n"
            f"A conversion has failed. Details below:\n\n"
            f"<tg-emoji emoji-id='5920344347152224466'>👤</tg-emoji> <b>User:</b> {safe_user_name} (<code>{user_id}</code>)\n"
            f"<tg-emoji emoji-id='5784982040432611567'>📦</tg-emoji> <b>Pack URL:</b> <a href=\"{pack_url}\">Click Here</a>\n"
            f"<tg-emoji emoji-id='5879785854284599288'>🆔</tg-emoji> <b>Log ID:</b> <code>{log_id}</code>\n"
            f"<tg-emoji emoji-id='5985433648810171091'>🛑</tg-emoji> <b>Error Type:</b> <code>{error_type}</code>\n"
            f"<tg-emoji emoji-id='5956561916573782596'>🗒️</tg-emoji> <b>Error Log:</b>\n"
            f"<pre><code>{safe_error_msg}</code></pre>"
        )
        await self._send_notification("conversion_failure", message)

    async def send_premium_purchase(self, user_id: int, user_display_name: str, payment_info: dict, days: int) -> None:
        """Sends a notification for a successful payment."""
        safe_user_name = html.escape(user_display_name)

        message = (
            f"<tg-emoji emoji-id='5947363097353130662'>⭐</tg-emoji> <b><u>Premium Purchase</u></b> <tg-emoji emoji-id='5947363097353130662'>⭐</tg-emoji>\n\n"
            f"A user has purchased premium. Details below:\n\n"
            f"<tg-emoji emoji-id='5920344347152224466'>👤</tg-emoji> <b>User:</b> {safe_user_name} (<code>{user_id}</code>)\n"
            f"<tg-emoji emoji-id='5927169041595634481'>💳</tg-emoji> <b>Payment Method:</b> <code>{payment_info.get('payment_method', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5778546023349621090'>💵</tg-emoji> <b>Currency:</b> <code>{payment_info.get('currency', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5985630530111020079'>💰</tg-emoji> <b>Amount:</b> <code>{payment_info.get('amount', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5776375003280838798'>🟢</tg-emoji> <b>Payment Status:</b> <code>{payment_info.get('status', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5776213190387961618'>⏳</tg-emoji> <b>Duration:</b> <code>{days} days</code>\n"
            f"<tg-emoji emoji-id='5879785854284599288'>🆔</tg-emoji> <b>Transaction ID:</b> <code>{html.escape(str(payment_info.get('transaction_id', 'N/A')))}</code>\n"
        )
        await self._send_notification("premium_purchase_success", message)
    
    async def send_premium_grant_failed(self, user_id: int, payment_info: dict, record_success: bool, refund_success: bool, error: str) -> None:
        """Sends a notification for a failure to grant premium after successful payment."""
        user = await self.client.get_entity(user_id)
        safe_user_name = html.escape(get_user_display_name(user))
        message = (
            f"<tg-emoji emoji-id='5427347926240221093'>🚨</tg-emoji> <b><u>Premium Grant Failed</u></b> <tg-emoji emoji-id='5427347926240221093'>🚨</tg-emoji>\n\n"
            f"A user has attempted to purchase premium. Details below:\n\n"
            f"<tg-emoji emoji-id='5920344347152224466'>👤</tg-emoji> <b>User:</b> {safe_user_name} (<code>{user_id}</code>)\n"
            f"<tg-emoji emoji-id='5927169041595634481'>💳</tg-emoji> <b>Payment Method:</b> <code>{payment_info.get('payment_method', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5778546023349621090'>💵</tg-emoji> <b>Currency:</b> <code>{payment_info.get('currency', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5985630530111020079'>💰</tg-emoji> <b>Amount:</b> <code>{payment_info.get('amount', 'N/A')}</code>\n"
            f"<tg-emoji emoji-id='5776375003280838798'>🟢</tg-emoji> <b>Payment Status:</b> <code>{payment_info.get('status', 'N/A')}</code>\n"
            f"""{"<tg-emoji emoji-id='5776375003280838798'>✅</tg-emoji>" if record_success else "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji>"} <b>Database Record:</b> <code>{record_success and 'Success' or 'Failed'}</code>\n"""
            f"""{"<tg-emoji emoji-id='5776375003280838798'>✅</tg-emoji>" if refund_success else "<tg-emoji emoji-id='5778527486270770928'>❌</tg-emoji>"} <b>Refund Status:</b> <code>{refund_success and 'Success' or 'Failed'}</code>\n"""
            f"<tg-emoji emoji-id='5879785854284599288'>🆔</tg-emoji> <b>Transaction ID:</b> <code>{html.escape(str(payment_info.get('transaction_id', 'N/A')))}</code>\n"
            f"<tg-emoji emoji-id='5956561916573782596'>🗒️</tg-emoji> <b>Error Log:</b> <pre>{html.escape(error)}</pre>\n"
        )
        await self._send_notification("premium_grant_failed", message)

    async def send_uncaught_exception(self, exc_info):
        """Sends a notification for an unhandled exception in the event loop."""
        exc_type, exc_value, exc_tb = exc_info
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        
        # Keep it under Telegram's message limit
        if len(tb_str) > 3500:
            tb_str = tb_str[:1750] + "\n...\n" + tb_str[-1750:]

        safe_tb = html.escape(tb_str)
        
        message = (
            f"<tg-emoji emoji-id='5276032951342088188'>💥</tg-emoji> <b><u>CRITICAL: Uncaught Exception</u></b> <tg-emoji emoji-id='5276032951342088188'>💥</tg-emoji>\n\n"
            f"An unhandled exception occurred in a background task. The bot might be in an unstable state.\n\n"
            f"<tg-emoji emoji-id='5956561916573782596'>🗒️</tg-emoji> <b>Traceback:</b>\n<pre><code>{safe_tb}</code></pre>"
        )
        await self._send_notification("uncaught_exception", message)
    
    async def send_cache_delete_failure(self, channel_id, message_ids, error):
        """Sends a notification for a failure in deleting cache files."""
        message = (
            f"<tg-emoji emoji-id='5215677343594457295'>⚠️</tg-emoji> <b><u>Cache Deletion Failure</u></b> <tg-emoji emoji-id='5215677343594457295'>⚠️</tg-emoji>\n\n"
            f"Failed to delete cache messages. Manual intervention may be required.\n\n"
            f"<b>Channel ID:</b> <code>{channel_id}</code>\n"
            f"<b>Message IDs:</b> <code>{message_ids}</code>\n"
            f"<b>Error:</b>\n<code>{html.escape(str(error))}</code>"
        )
        await self._send_notification("cache_delete_failure", message)

    async def send_message_delete_failure(self, chat_id, message_ids, custom_log, error):
        """Sends a notification for a generic message deletion failure."""
        message = (
            f"<tg-emoji emoji-id='5215677343594457295'>⚠️</tg-emoji> <b><u>Message Deletion Failure</u></b> <tg-emoji emoji-id='5215677343594457295'>⚠️</tg-emoji>\n\n"
            f"Failed to delete one or more messages.\n\n"
            f"<b>Context:</b> {html.escape(custom_log)}\n"
            f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"<b>Message IDs:</b> <code>{message_ids}</code>\n"
            f"<b>Error:</b>\n<code>{html.escape(str(error))}</code>"
        )
        await self._send_notification("message_delete_failure", message)

    async def send_cache_full_notification(self):
        """Sends a one-time critical alert when all cache channels are full."""
        message = (
            f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> <b><u>WARNING: Cache Channels Full</u></b> <tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji>\n\n"
            f"All configured cache channels have reached their limits.\n\n"
            f"<b><u>Consequences:</u></b>\n"
            f"The bot can no longer cache new sticker packs. Performance for frequently requested packs will be degraded until this is resolved.\n\n"
            f"<b><u>Action Required:</u></b>\n"
            f"Add new channel IDs to the <code>CACHE_CHANNEL_IDS</code> in the .env file.\n"
        )
        await self._send_notification("cache_channels_full", message)
