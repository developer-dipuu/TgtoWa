"""
Configuration file for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

# Telegram API Credentials
API_ID =  # Your API ID (must)
API_HASH = "" # Your API HASH (must)
BOT_TOKEN = "" # Your bot token (must)
BOT_USERNAME = "@" # Your bot username (will be used in welcome message)

# Required channels for membership verification
REQUIRED_CHANNELS = []             # 👉["@your_channels_here", "@your_channels_here"]👈  Use this format Only
                                   # Just add your channel here start and help message will be appended automaticlly
                                   # Just replace @your_support_group_here in the HELP_MESSAGE
# Sticker/emoji pack constraints
MAX_STICKERS_PER_PACK = 30

MAX_ICON_SIZE = 50 * 1024      # 50KB
STICKER_DIMENSIONS = (512, 512)
ICON_DIMENSIONS = (96, 96)

# File paths
TEMP_DIR = "temp"
OUTPUT_DIR = "output"

_channel_list_str = "\n".join([f"• {channel}" for channel in REQUIRED_CHANNELS])

# Messages

START_MESSAGE = f"""
🎉 <b>Welcome to {BOT_USERNAME}</b> 🎉

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
<blockquote>You'll need a helper app. We recommend <b>Sticker Maker for WhatsApp</b>.

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
<blockquote><b>Support Group</b>: <b>@your_support_group_here</b></blockquote>

"""

QUEUE_CHECK_MESSAGE = "📊 Queue Status\n\nYour position: {position}\nTotal in queue: {total}"

CHANNEL_JOIN_MESSAGE = f"""
❌ Access Denied!

To use this bot, you must join these channels/groups first:
{_channel_list_str}

After joining try again!
"""
