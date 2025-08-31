# setup_config.py
import json

print("🤖 Welcome to the Bot Configuration Setup!")
print("I'll ask you for some values to create your .env file.")
print("Press Enter to keep the default value if one is shown.\n")
print("⚠️ Warning: Enter values carefully — incorrect or malformed entries can break the bot. ⚠️\n")
# A little helper to get user input
def get_input(prompt, default=None):
    if default:
        return input(f"{prompt} [default: {default}]: ") or default
    return input(f"{prompt}: ")

# Writing the variables to the .env file
with open('.env', 'w') as f:
    f.write("# Telegram API Credentials\n")
    f.write(f"API_ID={get_input('Enter your API_ID')}\n")
    f.write(f"API_HASH={get_input('Enter your API_HASH')}\n")
    f.write(f"BOT_TOKEN={get_input('Enter your BOT_TOKEN')}\n")
    f.write(f"OWNER_ID={get_input('Enter your OWNER_ID')}\n\n")

    f.write("# Bot Configuration\n")
    f.write(f"SUPPORT_GROUP={get_input('Enter your Support Group username (e.g., @support_group)')}\n")
    f.write(f"NOTIFICATION_GROUP_ID={get_input('Enter your Notification Group ID')}\n\n")

    f.write("# Database Credentials\n")
    f.write(f"DB_NAME={get_input('Enter your DB_NAME', 'bot_db')}\n")
    f.write(f"DB_USER={get_input('Enter your DB_USER', 'bot_user')}\n")
    f.write(f"DB_PASSWORD={get_input('Enter your DB_PASSWORD', 'pwd123')}\n")
    f.write(f"DB_HOST={get_input('Enter your DB_HOST', 'localhost')}\n")
    f.write(f"DB_PORT={get_input('Enter your DB_PORT (Keep it default)', '5432')}\n\n")

    f.write("# Comma-separated lists (no spaces!)\n")
    f.write(f"CACHE_CHANNEL_IDS={get_input('Enter your CACHE_CHANNEL_IDS (comma-separated, e.g., -100123,-100456)')}\n")
    
    # Handling the complex REQUIRED_CHANNELS
    print("\n--- Required Channels Setup ---")
    print("For each channel, provide the name, link/username, and (for private ones) the ID.")
    print("Enter 'done' when you have no more channels to add.")
    
    channels = []
    while True:
        name = input("Channel Name (or 'done'): ")
        if name.lower() == 'done':
            break
        link = input(f"Link/Username for '{name}': ")
        channel_id = input(f"Private Channel ID for '{name}' (optional, press Enter if public): ")
        
        if channel_id:
            try:
                channels.append((name, link, int(channel_id)))
            except ValueError:
                print("⚠️ Invalid ID. It must be a number. Please try again.")
                continue
        else:
            channels.append((name, link))
    
    # We store this complex list as a JSON string in the .env file
    f.write(f"\n# Special JSON-formatted variables\n")
    f.write(f"REQUIRED_CHANNELS_JSON='{json.dumps(channels)}'\n")

    # Set default for ADMINS_TO_MENTION
    f.write(f"\n# Defaults can be overridden if needed\n")
    f.write("# ADMINS_TO_MENTION defaults to your OWNER_ID. You can override it here with a comma-separated list.\n")
    f.write("ADMINS_TO_MENTION=\n")


print("\n✅ Success! Your .env file has been created.")
