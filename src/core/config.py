"""
Configuration file for various settings of the bot. 
"""
import html
import os
import json
import math
from dotenv import load_dotenv

load_dotenv()

# Telegram API things (its must )
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# user id of owner and system
OWNER_ID = int(os.getenv("OWNER_ID")) # The owner
SYSTEM_USER_ID = 0

# ----- Premium Pricing Configuration ------
PREMIUM_PRICE_MONTHLY = 0.5
PREMIUM_PRICE_YEARLY = 5
PREMIUM_SAVINGS_PERCENT = math.floor((1 - (PREMIUM_PRICE_YEARLY / (PREMIUM_PRICE_MONTHLY * 12))) * 100)

# ----- Telegram Stars Pricing ------
PREMIUM_STARS_MONTHLY = 20   # Approximate equivalent of $0.50
PREMIUM_STARS_YEARLY = 200   # Approximate equivalent of $5.00

# ------ Conversion Limits ------
DAILY_LIMIT_REGULAR = 10 # (< 0 for unlimited)
DAILY_LIMIT_PREMIUM = 100 # (< 0 for unlimited)
MAX_CONCURRENT_REGULAR_REQUESTS = 1 
MAX_CONCURRENT_PREMIUM_REQUESTS = 3

#----- Timeouts and processing limits -------
# adjust according to your machine's internet speed and performance
DOWNLOAD_TIMEOUT = 10  # seconds to wait for a single sticker/emoji file to download (remmember 30 stickers are downloaded parallely at a time)
UPLOAD_TIMEOUT = 30    # seconds to wait for a .wastickers file to upload
ESTIMATED_TIME_MULTIPLIER = 3 # how many times of calculated estimated time should bot wait for the conversion of the pack
MAX_CONVERSION_SECONDS_REGULAR = 300  # 5 minutes. Max estimated time for non-premium users
DB_UPLOAD_TIMEOUT = 30
DB_DUMP_TIMEOUT = 30
MAX_DOWNLOAD_RETRIES = 2
MAX_UPLOAD_RETRIES = 2
CONVERSION_TIMEOUT = 30 # seconds to wait for a single sticker conversion process

# ------- Database Credentials -------
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

# ------File paths ---------- 
DATA_DIR = "storage/data"
TEMP_DIR = "storage/temp"
OUTPUT_DIR = "storage/output"
LOG_DIR = "storage/logs" # for /getlogs command and log backups

# ------ Cache settings ----------
CACHE_ENABLED = True # To use cache or not, owner can change it using the bot too but on bot restarts it will change to this default value
CACHE_SCORE_TIME_WEIGHT = 1.5   # Weight for conversion duration (in seconds)
CACHE_SCORE_REQUEST_WEIGHT = 1 # Weight for the number of times a pack is requested
MAX_FILES_PER_CACHE_CHANNEL = 95000
cache_ids_str = os.getenv("CACHE_CHANNEL_IDS", "")
CACHE_CHANNEL_IDS = [int(channel_id) for channel_id in cache_ids_str.split(',') if channel_id.strip()]

# ------ support group and required channels/groups ----------
# Support group for bot related queries (will be used in help message)
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP")
SUPPORT_GROUP_LINK = SUPPORT_GROUP if SUPPORT_GROUP.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else f"https://t.me/{SUPPORT_GROUP.lstrip("@")}"
# should be in format like ("Name", "@username", -12345675678) or ("Name", "link", -1212324141)
REQUIRED_CHANNELS = json.loads(os.getenv("REQUIRED_CHANNELS_JSON"))
# formatting properly for use
REQUIRED_CHANNELS_FORMATTED = [
    (name, link, id) if link.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else (name, f"https://t.me/{link.lstrip("@")}", id) for (name, link, id) in REQUIRED_CHANNELS
]

# Channel list converted to string 
_channel_list_str = "\n".join([f"• <b><a href=\"{channel[1]}\">{html.escape(channel[0])}</a></b>" for channel in REQUIRED_CHANNELS_FORMATTED])


# ------- Notification Settings --------

# The chat ID of the group where all notifications will be sent. Must be a negative number for groups.
# Get it by sending group to @username_to_id_bot
NOTIFICATION_GROUP_ID = int(os.getenv("NOTIFICATION_GROUP_ID"))
# A list of admin/owner user IDs to mention in critical notifications.
admins_str = os.getenv("ADMINS_TO_MENTION")
if admins_str:
    ADMINS_TO_MENTION = [int(admin_id) for admin_id in admins_str.split(',') if admin_id.strip()]
else:
    ADMINS_TO_MENTION = [OWNER_ID] # Default to the owner if not set


NOTIFICATIONS = {
    # For conversion failures during processing.
    "conversion_failure": {
        "enabled": True,
        "mention_admins": True,
    },
    # For unhandled exceptions that could crash a background task. I recommend you better keep it on.
    "uncaught_exception": {
        "enabled": True,
        "mention_admins": True,
    },
    # For new premium purchases.
    "premium_purchase_success": {
        "enabled": True,
        "mention_admins": True,
    },
    # For when the bot fails to add premium after successful payment.
    "premium_grant_failed": {
        "enabled": True,
        "mention_admins": True,
    },
    # For when caching is enabled but all cache channels are full.
    "cache_channels_full": {
        "enabled": True,
        "mention_admins": True,
    },
    # For when the bot fails to delete files from a cache channel.
    "cache_delete_failure": {
        "enabled": False,
        "mention_admins": False,
    },
    # For any other message deletion failures. Can be messsy so disabled by default.(change if you care for everything)
    "message_delete_failure": {
        "enabled": False,
        "mention_admins": False,
    }
}

# ------- Backup Settings --------

BACKUP_ENABLED = True # Master switch for all backups

# The chat ID for backups is the same as NOTIFICATION_GROUP_ID by default.
# You can override it here if you want a separate channel for backups.
BACKUP_GROUP_ID = NOTIFICATION_GROUP_ID

BACKUPS = {
    "database": {
        "enabled": True, # Toggle for database backups
    },
    "logs": {
        "enabled": True, # Toggle for log file backups
    }
}

# ========== Sticker/emoji pack constraints ==========
# Warning: dont change these unless you know what you are doing
MAX_STICKERS_PER_PACK = 30
MAX_WEBP_SIZE_KB = 495 # maximum size of each WEBP sticker in kilobytes
MAX_WEBP_FRAMES = 30 # frame cap for a WEBP sticker
WEBP_QUALITY = 80  # Default quality at the start of the compression
MAX_ICON_SIZE = 50 * 1024      # 50KB
STICKER_DIMENSIONS = (512, 512)
ICON_DIMENSIONS = (96, 96)


#================ Messages ===============

START_MESSAGE_FORMAT = f"""
<tg-emoji emoji-id="5472427507842032538">🎉</tg-emoji> <b>Welcome to {{bot_username}}</b>

I can convert any <b>Telegram sticker or emoji pack</b> directly into <b>WhatsApp stickers</b> for you. <tg-emoji emoji-id="5334998226636390258">✅</tg-emoji>

<b>To get started, you can either:</b>
• Send me a sticker or emoji pack link
• Or just send a sticker or emoji from the pack you want.

For a full guide on features and how to import the stickers to WhatsApp, please use the /help command.

<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> <b>Note:</b> You must be a member of following channels/groups to use this bot:
{_channel_list_str}
"""

HELP_MESSAGE = f"""
<tg-emoji emoji-id="5388953246486269495">📖</tg-emoji> <b>Help Guide</b>

<tg-emoji emoji-id="5785045099142450328">🤔</tg-emoji> <b>How to Convert a Pack?</b>
You have two simple options:
<blockquote>1.  <b>Send a Link</b>: Copy the sticker or emoji pack's link and send it to me.</blockquote>
<blockquote>2.  <b>Send a Sticker/Emoji</b>: Just send any sticker or emoji from the pack you want. I'll handle the rest.</blockquote>

---

<tg-emoji emoji-id="5334998226636390258">👉</tg-emoji> <b>How to Add Stickers to WhatsApp</b>

1. <tg-emoji emoji-id="5256186019136421048">📱</tg-emoji> <b>Install the App</b>: 
<blockquote>You'll need a helper app. We recommend <b>Sticker Maker</b>.

<tg-emoji emoji-id="5373130604147654226">🔗</tg-emoji> <b>Google Play Link</b>: <b><a href="https://play.google.com/store/apps/details?id=com.marsvard.stickermakerforwhatsapp">Click here</a></b>
<tg-emoji emoji-id="6271815741620621587">🔗</tg-emoji> <b>App Store Link</b>: <b><a href="https://apps.apple.com/us/app/sticker-maker-studio/id1443326857">Click here</a></b>
</blockquote>
2.  <tg-emoji emoji-id="5258134813302332906">📂</tg-emoji> <b>Open the File</b>: 
<blockquote>Once I send you the <code>.wastickers</code> file, tap on it here in Telegram.
</blockquote>
3.  <tg-emoji emoji-id="5258336354642697821">⬇️</tg-emoji> <b>Import</b>: 
<blockquote>Choose to open the file with the <b>Sticker Maker</b> app. Inside the app, tap "Add to my library" and then "Add to WhatsApp".
</blockquote>
That's it, your stickers are ready!

---

<tg-emoji emoji-id="5296739894914207637">✨</tg-emoji> <b>Explore More Features</b>
<blockquote>• Use /commands to see a full list of all available commands.
• Use /premium to check your premium status and learn about the benefits.</blockquote>

---

<tg-emoji emoji-id="6037579284837567462">📋</tg-emoji> <b>Important Notes</b>

• Packs with more than 30 stickers will be split into multiple files since WhatsApp supports only 30 stickers per pack.

<tg-emoji emoji-id="5256143829672672750">⏱️</tg-emoji> <b>Queue System</b>

• During busy times, your request is placed in a queue to ensure fair processing.
• You can check your position at any time using the /queue command.
• <tg-emoji emoji-id="6080171114007367607">⭐</tg-emoji> Premium users get priority and are moved to the front of the line!


<tg-emoji emoji-id="5443038326535759644">💬</tg-emoji> <b>Support</b>

If you run into any issues or have questions, please join our support group for assistance.
<blockquote><b>Support Group</b>: <b>{SUPPORT_GROUP}</b></blockquote>

"""

QUEUE_CHECK_MESSAGE = "<tg-emoji emoji-id='5258513401784573443'>📊</tg-emoji> <b>Queue Status</b>\n\nYour position: {position}\nTotal in queue: {total}"

CHANNEL_JOIN_MESSAGE = f"""
<tg-emoji emoji-id="5843952899184398024">❌</tg-emoji> <b>Access Denied!</b>

To use this bot, you must join these channels/groups first:
{_channel_list_str}

After joining try again!
"""

COMMANDS_MESSAGE = """
<tg-emoji emoji-id="5258093637450866522">🤖</tg-emoji> <b>Here are the commands you can use:</b>

• /start - <tg-emoji emoji-id="5247133031235329609">👋</tg-emoji> Displays the welcome message.
• /help - <tg-emoji emoji-id="5467461928647399673">📖</tg-emoji> Shows the detailed help guide.
• /queue - <tg-emoji emoji-id="5258513401784573443">📊</tg-emoji> Checks your current position in the conversion queue.
• /mystats - <tg-emoji emoji-id="5255900794653261326">📈</tg-emoji> Shows your usage statistics and current role.
• /premium - <tg-emoji emoji-id="5258165702707125574">⭐</tg-emoji> Displays your premium status and its benefits.
• /commands - <tg-emoji emoji-id="5258503720928288433">⚙️</tg-emoji> Shows a list of Available Commands
• /suggest - <tg-emoji emoji-id="5255813559572508065">✨</tg-emoji> Get recommendations for popular packs.
• /contact - <tg-emoji emoji-id="5260535596941582167">📩</tg-emoji> Send a message to the bot administrators.

Just send any of these commands to get started!
"""

CONTACT_PROMPT_MESSAGE = """
<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> <b>Contact an Admin</b>

This will forward your next message to the entire admin team. Please be patient for a response.

<b>Please Note:</b>
- This feature is for genuine queries and feedback only.
- Abusing this feature for spam may result in a ban.
"""

CONTACT_SUCCESS_MESSAGE = '<tg-emoji emoji-id="5336985409220001678">✅</tg-emoji> <b>Message Sent!</b>\n\nYour message has been forwarded to the admin team. If a reply is needed, they will contact you directly through me.'

CONTACT_FAILURE_MESSAGE = '<tg-emoji emoji-id="5019523782004441717">❌</tg-emoji> <b>Failed to Send Message!</b>\n\nYour message could not be forwarded to the admin team. Please try again later.'

CONTACT_ADMIN_REPLY_HEADER = '<tg-emoji emoji-id="5406631276042002796">📨</tg-emoji> <b>A reply from the admin team <tg-emoji emoji-id="4992796097442218769">👇</tg-emoji></b>'

CONTACT_ADMIN_NOTIFICATION_HEADER = """
<tg-emoji emoji-id="5253742260054409879">📩</tg-emoji> <b>New User Message</b>

<tg-emoji emoji-id="5258503720928288433">📄</tg-emoji> <b>Contact ID:</b> <code>{contact_id}</code>
<tg-emoji emoji-id="5258011929993026890">👤</tg-emoji> <b>From:</b> {user_display_name}
- <b>User ID:</b> <code>{user_id}</code>
- <b>Status:</b> {role}
- <b>Stats:</b>
<tg-emoji emoji-id="5260416304224936047">✅</tg-emoji> Succeeded: <code>{succeeded}</code>
<tg-emoji emoji-id="5260342697075416641">❌</tg-emoji> Failed: <code>{failed}</code>
<tg-emoji emoji-id="5258318620722733379">🚫</tg-emoji> Cancelled: <code>{cancelled}</code>
<tg-emoji emoji-id="5258330865674494479">📍</tg-emoji> Total: <code>{total}</code>
"""
