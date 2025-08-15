import html
"""
Configuration file for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""
# ========= Should be changed ===================

# Telegram API things (its must )
API_ID = 12345678 #  Replace with your API ID (must)
API_HASH = "" # Your API HASH (must)
BOT_TOKEN = "" # Your bot token (must)

OWNER_ID = 1234567890 # Replace with your Telegram User ID (needed for approving admins and users)

# ‼️ Must READ
''' Required channels to use this bot (leave empty if you don't want to force user to join)
For public channel/group add a tuple like this ("Name", "@username")
For private channel/group add a tuple like this ("Name", "link", -1212324141) 
-1212324141 is your channel/group id, get it by forwarding any of your channel/group's message to @username_to_id_bot (or @MissRose_bot and reply /id to that message)
 ⚠️⚠️⚠️ [("Public_Channel_Name", "@channel_1_username"), ("Private_Channel_Name", "https://t.me/+abcdefghijk", -34876824274 )] ⚠️⚠️⚠️  Use this format Only '''

REQUIRED_CHANNELS = []  # channels that users must join to use the bot


# Support group for bot related queries (will be used in help message)
SUPPORT_GROUP = "@your_support_group_here"  # if you're done editing this far you are good to go (you may review DOWNLOAD_TIMEOUT but dont change other shits unless you know what you are doing)


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
MAX_DOWNLOAD_RETRIES = 3

# ------File paths ---------- 
DATA_DIR = "data"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"
LOG_DIR = "~/screenlogs" # if you have setup the logging system like screen's logging and use logrotate to manage logs (for /getlogs command)
DB_PATH = f"{DATA_DIR}/bot_data.db" # its by default in the data directory but if you change that it should get changed automatically unless you mess it up, its needed for the /getdb command to work

# ------ Cache settings ----------
CACHE_ENABLED = True # To use cache or not, owner can change it using the bot too but on bot restarts it will change to this default value
CACHE_DIR = "cache"
MAX_CACHED_PACKS = 1000 
CACHE_SCORE_TIME_WEIGHT = 1.5   # Weight for conversion duration (in seconds)
CACHE_SCORE_REQUEST_WEIGHT = 1 # Weight for the number of times a pack is requested

# ------ formatting a few things ----------
# formatting properly for use
SUPPORT_GROUP_LINK = SUPPORT_GROUP if SUPPORT_GROUP.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else f"https://t.me/{SUPPORT_GROUP.lstrip("@")}"
REQUIRED_CHANNELS_FORMATTED = [
    (name, link, *rest) if link.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else (name, f"https://t.me/{link.lstrip("@")}", *rest) for (name, link, *rest) in REQUIRED_CHANNELS
]

# Channel list converted to string 
_channel_list_str = "\n".join([f"• <b><a href=\"{channel[1]}\">{html.escape(channel[0])}</a></b>" for channel in REQUIRED_CHANNELS_FORMATTED])



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
📍 Total: <code>{total}</code>
"""
