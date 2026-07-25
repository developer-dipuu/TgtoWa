"""
This script is used to set up the environment variables for the bot.
It is a CLI application that prompts the user to enter the values for the environment variables.
It then writes the values to a .env file.
"""
import json

class Colors:
    RESET = '\033[0m'
    
    BOLD          = '\033[1m'
    UNDERLINE     = '\033[4m'

    FG_BLACK   = '\033[30m'
    FG_RED     = '\033[31m'
    FG_YELLOW  = '\033[33m'

    BG_GREEN   = '\033[42m'
    BG_BLUE    = '\033[44m'
    BG_MAGENTA = '\033[45m' 


# A little helper to get user input
def get_input(prompt, default=None):
    if default:
        return input(f"> {prompt} [default: {default}]: ") or default
    return input(f"> {prompt}: ")

print(f"{Colors.BOLD}{Colors.BG_MAGENTA}{Colors.FG_BLACK} Bot Configuration Setup!{Colors.RESET}\n")
print(f"{Colors.BOLD}Read the instructions and enter the values carefully to create your .env file.{Colors.RESET}")
print(f"{Colors.BOLD}Press Enter to keep the default value if one is shown.{Colors.RESET}\n")
print(f"{Colors.BOLD}{Colors.FG_YELLOW}Warning: Enter values carefully — incorrect or malformed entries can break the bot.{Colors.RESET}\n")


# writing the variables to the .env file
with open('.env', 'w') as f:
    # Telegram API credentials
    print(f"{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Telegram API credentials.{Colors.RESET}")
    f.write("# Telegram API Credentials\n")
    f.write(f"API_ID={get_input('Enter your API ID')}\n")
    f.write(f"API_HASH={get_input('Enter your API HASH')}\n")
    f.write(f"BOT_TOKEN={get_input('Enter your BOT TOKEN')}\n\n")

    # Database credentials
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Database credentials.{Colors.RESET}")
    print(f"{Colors.BOLD}Enter the credentials of an empty instance of postgresql database.{Colors.RESET}")
    f.write("# Database Credentials\n")
    f.write(f"DB_NAME={get_input('Enter your database name (e.g. bot_db)')}\n")
    f.write(f"DB_PASSWORD='{get_input('Enter your database password (e.g. pwd123)')}'\n")
    f.write(f"DB_USER={get_input('Enter your database user (e.g. bot_user)')}\n")
    f.write(f"DB_HOST={get_input('Enter your database host', default='localhost')}\n")
    f.write(f"DB_PORT={get_input('Enter your database port', default='5432')}\n\n")

    # Bot configuration
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Bot Configuration.{Colors.RESET}")
    f.write("# Bot Configuration\n")
    f.write(f"OWNER_ID={get_input('Enter owner\'s telegram ID (e.g. 1234567890)')}\n")
    print(f"\n{Colors.BOLD}Support Group is the chat where users can ask queries and it will be used in various interfaces of the bot and it must be a public group.{Colors.RESET}")
    f.write(f"SUPPORT_GROUP={get_input('Enter your Support Group username (e.g., @support_group)')}\n")
    print(f"\n{Colors.BOLD}Notification group is the chat where all notifications realted to bot, errors and backups will be sent. (e.g. -1234567890){Colors.RESET}")
    f.write(f"NOTIFICATION_GROUP_ID={get_input('Enter your Notification Group ID')}\n\n")

    # Cache groups
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Cache Groups Setup.{Colors.RESET}")
    print(f"{Colors.BOLD}Cache groups are the groups where all the converted packs are stored. It is recommended to be private.{Colors.RESET}")
    print(f"{Colors.BOLD}Enter comma separated cache group IDs with no spaces (e.g., -100123564646,-10045645645){Colors.RESET}")
    f.write("# Comma separated lists (no spaces)\n")
    f.write(f"CACHE_CHANNEL_IDS={get_input('Enter your cache group IDs')}\n")
    
    # Required channels
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Required Channels Setup{Colors.RESET}")
    print(f"{Colors.BOLD}Required channels are the channels/groups which are required to be joined by users to use the bot.{Colors.RESET}")
    print(f"{Colors.BOLD}For each channel/group, provide the name, link/username, and ID.{Colors.RESET}")
    print(f"{Colors.BOLD}Press {Colors.UNDERLINE}Enter{Colors.RESET}{Colors.BOLD} to skip or when you have no more channels/groups to add.{Colors.RESET}")
    
    channels = []
    while True:
        print()
        name = input("> Channel/Group Name (or press Enter): ")
        if name == '':
            break
        link = input(f"> Link/Username for '{name}': ")
        channel_id = input(f"> Channel ID for '{name}': ")
        
        try:
            channels.append((name, link, int(channel_id)))
        except ValueError:
            print(f"{Colors.FG_RED}Invalid ID. It must be a number. Please try again.{Colors.RESET}")
        
    
    # Store the list as a json string in the .env file
    f.write(f"\n# Special json variables\n")
    f.write(f"REQUIRED_CHANNELS_JSON='{json.dumps(channels)}'\n")

    # Default ADMINS_TO_MENTION
    f.write(f"\n# Defaults can be overridden if needed\n")
    f.write("# ADMINS_TO_MENTION defaults to your OWNER_ID. You can override it here with a comma-separated list.\n")
    f.write("ADMINS_TO_MENTION=\n")


print(f"\n{Colors.BOLD}{Colors.BG_GREEN}{Colors.FG_BLACK}Success! Your .env file has been created.{Colors.RESET}")
