"""
Configuration file for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

# Telegram API Credentials
API_ID = 20430589 # Your API ID (must)
API_HASH = "d8a15ae0a1320abbc27c5482c62e8ed0" # Your API HASH (must)
BOT_TOKEN = "8267507113:AAGytCxuWx9hjfjOCjihHJrU1zV9SwCcy8Y" # Your bot token (must)

OWNER_ID = 2128132096 # 👈 Replace with your Telegram User ID (needed for approving admins and users)

# ‼️ Must READ
''' Required channels to use this bot (leave empty if you don't want to force user to join)
For public channel/group add a tuple like this ("Name", "@username")
For private channel/group add a tuple like this ("Name", "link", -1212324141) 
-1212324141 is your channel/group id get it by forwarding any of your channel/group's message to @username_to_id_bot (or @MissRose_bot and reply /id to that message)
 ⚠️⚠️⚠️ [("Public_Channel_Name", "@channel_1_username"), ("Private_Channel_Name", "https://t.me/+abcdefghijk", -34876824274 )] ⚠️⚠️⚠️  Use this format Only '''

REQUIRED_CHANNELS = [("Test", "https://t.me/+Qhd_ao_OjUU2OGFl", -1001552682716), ("Contact", "@Please_contact_me_here")]


# Support group foor bot related queries (will be used in help message)
SUPPORT_GROUP_LINK = "@your_support_group_here"    # if you're done editing this far you are good to go

# Sticker/emoji pack constraints
MAX_STICKERS_PER_PACK = 30
WEBP_QUALITY = 80  # Default qualty at the start of the compression
MAX_ICON_SIZE = 50 * 1024      # 50KB
STICKER_DIMENSIONS = (512, 512)
ICON_DIMENSIONS = (96, 96)

# File paths
TEMP_DIR = "temp"
OUTPUT_DIR = "output"

REQUIRED_CHANNELS_FORMATTED = [
    (name, link, *rest) if link.startswith(('https://t.me/', 'http://t.me/', 'https://telegram.me/', 'http://telegram.me/', 't.me/')) else (name, f"https://t.me/{link.lstrip("@")}", *rest) for (name, link, *rest) in REQUIRED_CHANNELS
]

# Channel list converted to string 
_channel_list_str = "\n".join([f"• <b><a href=\"{channel[1]}\">{channel[0]}</a></b>" for channel in REQUIRED_CHANNELS_FORMATTED])

# Messages

START_MESSAGE_FORMAT = f"""
🎉 <b>Welcome to {{bot_username}}</b> 🎉

I can convert any <b>Telegram sticker or emoji pack</b> directly into <b>WhatsApp stickers</b> for you.

<b>To get started, you can either:</b>
• Send me a sticker or emoji pack link
• Or just send a sticker or emoji from the pack you want.

For a full guide on features and how to import the stickers to WhatsApp, please use the /help command.

⚠️ <b>Note:</b> You must join the following channels to use this bot:
{_channel_list_str}
"""

HELP_MESSAGE = f"""
📖 <b>Help Guide</b>

🤔 <b>How to Convert a Pack?</b>
You have two simple options:
<blockquote>1.  <b>Send a Link</b>: Copy the sticker or emoji pack's link and send it to me.</blockquote>
<blockquote>2.  <b>Send a Sticker/Emoji</b>: Just send any sticker or emoji from the pack you want. I'll handle the rest.</blockquote>

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

• Packs with more than 30 stickers will be split into multiple files.

⏱️ <b>Queue System</b>

• During busy times, you'll be placed in a queue. Use the "Check Queue" button to see your position.

💬 <b>Support</b>

If you run into any issues or have questions, please join our support group for assistance.
<blockquote><b>Support Group</b>: <b>{SUPPORT_GROUP_LINK}</b></blockquote>

"""

QUEUE_CHECK_MESSAGE = "📊 Queue Status\n\nYour position: {position}\nTotal in queue: {total}"

CHANNEL_JOIN_MESSAGE = f"""
❌ Access Denied!

To use this bot, you must join these channels/groups first:
{_channel_list_str}

After joining try again!
"""
