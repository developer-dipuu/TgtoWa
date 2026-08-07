"""
This script is used to set up the environment variables for the bot and is the highly recommended way to do it.
It is a CLI application that prompts the user to enter the values for the environment variables.
It then writes the values to a .env file.
"""
import os
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

def escape_value(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')

# A little helper to get user input
def get_input(prompt, allow_empty=False, default=None, sensitive=False, empty_warning=None, escape=False):
    if not allow_empty and default is None:
        input_str = ""
        i = 0
        while not input_str:
            if i > 0:
                print(f"{Colors.FG_YELLOW}This cannot be empty. Please enter a value.{Colors.RESET}")
            input_str = input(f"> {prompt} {f'[default: {default}]' if default is not None else ''}: ")
            if not sensitive:
                input_str = input_str.strip()
            i += 1
    else:
        input_str = input(f"> {prompt} {f'[default: {default}]' if default is not None else ''}: ")
        if not sensitive:
            input_str = input_str.strip()
    
    if not input_str and default is not None:
        input_str = default

    if not input_str and empty_warning:
        print(f"{Colors.FG_YELLOW}{empty_warning}{Colors.RESET}")

    return escape_value(input_str) if escape else input_str

print(f"{Colors.BOLD}{Colors.BG_MAGENTA}{Colors.FG_BLACK} Bot Configuration Setup!{Colors.RESET}\n")
print(f"{Colors.BOLD}Read the instructions and enter the values carefully to create your .env file.{Colors.RESET}")
print(f"{Colors.BOLD}Press Enter to keep the default value if one is shown.{Colors.RESET}\n")
print(f"{Colors.BOLD}Note: This script is supposed to be run from the root directory of the project.{Colors.RESET}\n")
print(f"{Colors.BOLD}{Colors.FG_YELLOW}Warning: Enter values carefully — incorrect or malformed entries can break the bot.{Colors.RESET}\n")


# writing the variables to the .env file
with open('.env', 'w') as f:
    f.write("# WARNING! It is recommended to not edit this file manually and set the env variables using the env_setup script, specially for the fields marked as escaping required.\n\n")
    
    # Telegram API credentials ---------------------------------
    print(f"{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Telegram API credentials.{Colors.RESET}")
    f.write("# Telegram API Credentials\n")
    f.write(f"API_ID={get_input('Enter your API ID')}\n")
    f.write(f"API_HASH={get_input('Enter your API HASH')}\n")
    f.write(f"BOT_TOKEN={get_input('Enter your BOT TOKEN')}\n\n")

    # Database credentials -------------------------------------
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Database credentials.{Colors.RESET}")

    DB_CRED_FILE = "storage/.db_credentials"
    db_creds = None

    # checking if the db credentials file exists
    if os.path.isfile(DB_CRED_FILE):
        try:
            with open(DB_CRED_FILE) as cred_f:
                db_creds = json.load(cred_f)
        except (json.JSONDecodeError, OSError):
            db_creds = None

    if db_creds:
        print(f"\n{Colors.BOLD}Found existing database credentials from setup, it is recommended to use it unless you have any specific reason to change it.{Colors.RESET}")
        print(f"  DB_NAME: {db_creds.get('DB_NAME')}")
        print(f"  DB_USER: {db_creds.get('DB_USER')}")
        print(f"  DB_HOST: {db_creds.get('DB_HOST')}")
        print(f"  DB_PORT: {db_creds.get('DB_PORT')}")
        print(f"  DB_PASS: {'*' * len(db_creds.get('DB_PASS', ''))}")
        use_existing = get_input("Use these credentials? (y/n)", default="y").lower().startswith("y")
    else:
        use_existing = False

    if use_existing:
        f.write("# Database Credentials (escaping required for name, password, user, host)\n")
        f.write(f'DB_NAME="{escape_value(db_creds["DB_NAME"])}"\n')
        f.write(f'DB_PASSWORD="{escape_value(db_creds["DB_PASS"])}"\n')
        f.write(f'DB_USER="{escape_value(db_creds["DB_USER"])}"\n')
        f.write(f'DB_HOST="{escape_value(db_creds["DB_HOST"])}"\n')
        f.write(f"DB_PORT={db_creds['DB_PORT']}\n\n")
    else:
        print(f"{Colors.BOLD}Enter the credentials of an empty instance of postgresql database.{Colors.RESET}")
        f.write("# Database Credentials (escaping required for name, password, user, host)\n")
        f.write(f'DB_NAME="{get_input("Enter your database name (e.g. bot_db)", escape=True)}"\n') # escape handles \ and " and also dont mess with the quotes of lines with escape=True
        f.write(f'DB_PASSWORD="{get_input("Enter your database password (e.g. pwd123)", allow_empty=True, sensitive=True, escape=True)}"\n') #same here
        f.write(f'DB_USER="{get_input("Enter your database user (e.g. bot_user)", escape=True)}"\n') #same here
        f.write(f'DB_HOST="{get_input("Enter your database host", default="localhost", escape=True)}"\n') #same here
        f.write(f"DB_PORT={get_input('Enter your database port', default='5432')}\n\n")

    # Bot configuration --------------------------------
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Bot Configuration.{Colors.RESET}")
    f.write("# Bot Configuration\n")
    f.write(f"OWNER_ID={get_input('Enter owner\'s telegram ID (e.g. 1234567890)')}\n")
    print(f"\n{Colors.BOLD}Support Group is the chat where users can ask queries and it will be used in various interfaces of the bot and it must be a public group.{Colors.RESET}")
    f.write(f"SUPPORT_GROUP={get_input('Enter your Support Group username (e.g., @support_group)')}\n")
    print(f"\n{Colors.BOLD}Notification group is the chat where all notifications realted to bot, errors and backups will be sent. (e.g. -1234567890){Colors.RESET}")
    f.write(f"NOTIFICATION_GROUP_ID={get_input('Enter your Notification Group ID')}\n\n")

    # Cache groups -----------------------------------
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Cache Groups Setup.{Colors.RESET}")
    print(f"{Colors.BOLD}Cache groups are the groups where all the converted packs are stored. It is recommended to be private.{Colors.RESET}")
    print(f"{Colors.BOLD}Enter comma separated cache group IDs with no spaces (e.g., -100123564646,-10045645645){Colors.RESET}")
    f.write("# Comma separated lists (no spaces)\n")
    f.write(f"CACHE_CHANNEL_IDS={get_input('Enter your cache group IDs', allow_empty=True, empty_warning='Warning: Not entering any cache group ID will make the bot unable to store the converted packs which will degrade user experience and performance.')}\n")
    
    # Required channels --------------------------------
    print(f"\n{Colors.BOLD}{Colors.BG_BLUE}{Colors.FG_BLACK}Required Channels Setup{Colors.RESET}")
    print(f"{Colors.BOLD}Required channels are the channels/groups which are required to be joined by users to use the bot.{Colors.RESET}")
    print(f"{Colors.BOLD}For each channel/group, provide the name, link/username, and ID.{Colors.RESET}")
    print(f"{Colors.BOLD}Press {Colors.UNDERLINE}Enter{Colors.RESET}{Colors.BOLD} to skip or when you have no more channels/groups to add.{Colors.RESET}")
    
    # we first create a raw list object with no escaping thigs just normal list with user input
    channels = []
    while True:
        print()
        name = get_input("Channel/Group Name (or press Enter)", allow_empty=True)
        if name == '':
            break
        link = get_input(f"Link/Username for '{name}'")
        
        while True:
            try:
                channel_id = get_input(f"Channel ID for '{name}'")
                channels.append((name, link, int(channel_id)))
                break
            except ValueError:
                print(f"{Colors.FG_RED}Invalid ID. It must be a number. Please try again.{Colors.RESET}")
        
    
    # Store the list as a json string with escaping in the .env file
    f.write(f"\n# Special json variables (escaping required)\n")
    # json.dumps() itself escapes " and \ but when we will load using json.loads() it expects that escaped string
    json_str = json.dumps(channels)
    # so we escape them again so that dotenv doesn't parse them and json.loads() receives the same string json.dumps() created
    safe_json_str = json_str.replace('\\', '\\\\').replace('"', '\\"')
    f.write(f'REQUIRED_CHANNELS_JSON="{safe_json_str}"\n') # dont mess with quotes here too

    # Default ADMINS_TO_MENTION --------------------------------
    f.write(f"\n# Defaults can be overridden if needed\n")
    f.write("# ADMINS_TO_MENTION defaults to your OWNER_ID. You can override it here with a comma-separated list.\n")
    f.write("ADMINS_TO_MENTION=\n")


print(f"\n{Colors.BOLD}{Colors.BG_GREEN}{Colors.FG_BLACK}Success! Your .env file has been created.{Colors.RESET}")
