import html
import os
import json
from dotenv import load_dotenv

load_dotenv()

"""
Configuration file for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""
# ========= Should be changed ===================

# Telegram API things (its must )
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = int(os.getenv("OWNER_ID")) # The owner

''' Required channels to use this bot (leave empty if you don't want to force user to join)
This is handled automatically by env_setup.py.
For public channel/group add a tuple like this ("Name", "@username")
For private channel/group add a tuple like this ("Name", "link", -1212324141) 
-1212324141 is your channel/group id, get it by forwarding any of your channel/group's message to @username_to_id_bot (or @MissRose_bot and reply /id to that message)
[("Public_Channel_Name", "@channel_1_username"), ("Private_Channel_Name", "https://t.me/+abcdefghijk", -34876824274 )]  Use this format Only '''

REQUIRED_CHANNELS = json.loads(os.getenv("REQUIRED_CHANNELS_JSON"))

# Support group for bot related queries (will be used in help message)
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP")

# =============================================


# ------ Sticker/emoji pack constraints ------
# dont change these
MAX_STICKERS_PER_PACK = 30
WEBP_QUALITY = 80  # Default quality at the start of the compression
MAX_ICON_SIZE = 50 * 1024      # 50KB
STICKER_DIMENSIONS = (512, 512)
ICON_DIMENSIONS = (96, 96)
# you may change this if you want
MAX_CONCURRENT_REGULAR_REQUESTS = 1 
MAX_CONCURRENT_PREMIUM_REQUESTS = 3

#----- Timeouts and processing limits -------
DOWNLOAD_TIMEOUT = 30  # seconds to wait for a sticker/emoji file to download  # 👉 if you run the bot on a server or on any machine with good & stable internet speed then i recommend to make it 10
UPLOAD_TIMEOUT = 60    # seconds to wait for a .wastickers file to upload
MAX_CONVERSION_SECONDS_REGULAR = 300  # 5 minutes. Max estimated time for non-premium users
DB_UPLOAD_TIMEOUT = 30
DB_DUMP_TIMEOUT = 30
MAX_DOWNLOAD_RETRIES = 3

# ------- Database Credentials -------
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

# ------File paths ---------- 
DATA_DIR = "data"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"
LOG_DIR = "~/screenlogs" # if you have setup the logging system like screen's logging and use logrotate to manage logs (for /getlogs command)

# ------ Cache settings ----------
CACHE_ENABLED = True # To use cache or not, owner can change it using the bot too but on bot restarts it will change to this default value
CACHE_SCORE_TIME_WEIGHT = 1.5   # Weight for conversion duration (in seconds)
CACHE_SCORE_REQUEST_WEIGHT = 1 # Weight for the number of times a pack is requested
MAX_FILES_PER_CACHE_CHANNEL = 95000
cache_ids_str = os.getenv("CACHE_CHANNEL_IDS", "")
CACHE_CHANNEL_IDS = [int(channel_id) for channel_id in cache_ids_str.split(',') if channel_id.strip()]

# ------ formatting a few things ----------
# formatting properly for use
SUPPORT_GROUP_LINK = SUPPORT_GROUP if SUPPORT_GROUP.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else f"https://t.me/{SUPPORT_GROUP.lstrip("@")}"
REQUIRED_CHANNELS_FORMATTED = [
    (name, link, *rest) if link.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else (name, f"https://t.me/{link.lstrip("@")}", *rest) for (name, link, *rest) in REQUIRED_CHANNELS
]

# Channel list converted to string 
_channel_list_str = "\n".join([f"• <b><a href=\"{channel[1]}\">{html.escape(channel[0])}</a></b>" for channel in REQUIRED_CHANNELS_FORMATTED])


# ========= Notification Settings =============

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
    # For when the bot fails to delete files from a cache channel.
    "cache_delete_failure": {
        "enabled": True,
        "mention_admins": False,
    },
    # For any other message deletion failures. Can be messsy so disabled by default.(change if you care for everything)
    "message_delete_failure": {
        "enabled": False,
        "mention_admins": False,
    }
}

# ============ Backup Settings ================

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


#===================== You need not to change thses things unless you want to edit default messages ======================================

#================ Messages ===============

START_MESSAGE_FORMAT = f"""
🎉 <b>Welcome to {{bot_username}}</b> 🎉

I can convert any <b>Telegram sticker or emoji pack</b> directly into <b>WhatsApp stickers</b> for you.

<b>To get started, you can either:</b>
• Send me a sticker or emoji pack link
• Or just send a sticker or emoji from the pack you want.

For a full guide on features and how to import the stickers to WhatsApp, please use the /help command.

⚠️ <b>Note:</b> You must be a member of following channels/groups to use this bot:
{_channel_list_str}
"""

HELP_MESSAGE = f"""
📖 <b>Help Guide</b>

🤔 <b>How to Convert a Pack?</b>
You have two simple options:
<blockquote>1.  <b>Send a Link</b>: Copy the sticker or emoji pack's link and send it to me.</blockquote>
<blockquote>2.  <b>Send a Sticker/Emoji</b>: Just send any sticker or emoji from the pack you want. I'll handle the rest.</blockquote>

---

✨ <b>Explore More Features</b>
<blockquote>• Use /commands to see a full list of all available commands.
• Use /premium to check your premium status and learn about the benefits.</blockquote>

---

👉 <b>How to Add Stickers to WhatsApp</b>

1.  <b>📱 Install the App</b>: 
<blockquote>You'll need a helper app. We recommend <b>Sticker Maker</b>.

<b>🔗Google Play Link</b>: <b><a href="https://play.google.com/store/apps/details?id=com.marsvard.stickermakerforwhatsapp">Click here</a></b>
<b>🔗App Store Link</b>: <b><a href="https://apps.apple.com/us/app/sticker-maker-studio/id1443326857">Click here</a></b>
</blockquote>
2.  <b>📂 Open the File</b>: 
<blockquote>Once I send you the <code>.wastickers</code> file, tap on it here in Telegram.
</blockquote>
3.  <b>⬇️ Import</b>: 
<blockquote>Choose to open the file with the <b>Sticker Maker</b> app. Inside the app, tap "Add to my library" and then "Add to WhatsApp".
</blockquote>
That's it, your stickers are ready!

---

📋 <b>Important Notes</b>

• Packs with more than 30 stickers will be split into multiple files since WhatsApp supports only 30 stickers per pack.

⏱️ <b>Queue System</b>

• During busy times, your request is placed in a queue to ensure fair processing.
• You can check your position at any time using the /queue command.
• ⭐ Premium users get priority and are moved to the front of the line!


💬 <b>Support</b>

If you run into any issues or have questions, please join our support group for assistance.
<blockquote><b>Support Group</b>: <b>{SUPPORT_GROUP}</b></blockquote>

"""

QUEUE_CHECK_MESSAGE = "📊 Queue Status\n\nYour position: {position}\nTotal in queue: {total}"

CHANNEL_JOIN_MESSAGE = f"""
❌ Access Denied!

To use this bot, you must join these channels/groups first:
{_channel_list_str}

After joining try again!
"""

COMMANDS_MESSAGE = """
🤖 <b>Here are the commands you can use:</b>

• /start - 👋 Displays the welcome message.
• /help - 📖 Shows the detailed help guide.
• /queue - 📊 Checks your current position in the conversion queue.
• /mystats - 📈 Shows your usage statistics and current role.
• /premium - ⭐ Displays your premium status and its benefits.
• /commands - ⚙️ Shows a list of Available Commands
• /suggest - ✨ Get recommendations for popular packs.
• /contact - 📨 Send a message to the bot administrators.

Just send any of these commands to get started!
"""

CONTACT_PROMPT_MESSAGE = """
⚠️ <b>Contact an Admin</b>

This will forward your next message to the entire admin team. Please be patient for a response.

<b>Please Note:</b>
- This feature is for genuine queries and feedback only.
- Abusing this feature for spam may result in a ban.

Click "✉️ Send Message" to proceed or "❌ Cancel" to go back.
"""

CONTACT_SUCCESS_MESSAGE = "✅ <b>Message Sent!</b>\n\nYour message has been forwarded to the admin team. If a reply is needed, they will contact you directly through me."

CONTACT_ADMIN_REPLY_HEADER = "📨 <b>A reply from the admin team 👇</b>"

CONTACT_ADMIN_NOTIFICATION_HEADER = """
📩 <b>New User Message</b>

📄 <b>Contact ID:</b> <code>{contact_id}</code>
👤 <b>From:</b> {user_display_name}
- <b>User ID:</b> <code>{user_id}</code>
- <b>Status:</b> {role}
- <b>Stats:</b>
✅ Succeeded: <code>{succeeded}</code>
❌ Failed: <code>{failed}</code>
🚫 Cancelled: <code>{cancelled}</code>
📍 Total: <code>{total}</code>
"""
