#!/bin/bash
# This scripts sets up an "ubuntu" vps for the bot automatically

# Stop immediately if any command fails
set -e
trap 'error "Script failed at line $LINENO: $BASH_COMMAND"' ERR

# Varibles for styling the output
RESET='\033[0m'

BOLD='\033[1m'
UNDERLINE='\033[4m'

FG_BLACK='\033[30m'
FG_RED='\033[31m'
FG_GREEN='\033[32m'
FG_YELLOW='\033[33m'
FG_BLUE='\033[34m'
FG_MAGENTA='\033[35m'
FG_CYAN='\033[36m'
FG_WHITE='\033[37m'

BG_BLACK='\033[40m'
BG_RED='\033[41m'
BG_GREEN='\033[42m'
BG_YELLOW='\033[43m'
BG_BLUE='\033[44m'
BG_MAGENTA='\033[45m'
BG_CYAN='\033[46m'
BG_WHITE='\033[47m'


start_heading() {
    local text="$*"
    local width=$(( ${#text} + 4 ))

    printf "╔"
    printf '═%.0s' $(seq 1 "$width")
    printf "╗\n"

    printf "║ %b%s%b ║\n" \
        "${BOLD}${BG_MAGENTA}${FG_BLACK}" \
        " $text " \
        "${RESET}"

    printf "╚"
    printf '═%.0s' $(seq 1 "$width")
    printf "╝\n"
}

finish_heading() {
    local text="$*"
    local width=$(( ${#text} + 4 ))

    printf "╔"
    printf '═%.0s' $(seq 1 "$width")
    printf "╗\n"

    printf "║ %b%s%b ║\n" \
        "${BOLD}${BG_GREEN}${FG_BLACK}" \
        " $text " \
        "${RESET}"

    printf "╚"
    printf '═%.0s' $(seq 1 "$width")
    printf "╝\n"
}

heading() {
    printf "\n%b\n" "${BOLD}${BG_MAGENTA}${FG_BLACK} $* ${RESET}"
}

subheading() {
    printf "\n%b\n" "${BOLD}${BG_BLUE}${FG_BLACK} $* ${RESET}"
}

success() {
    printf "%b\n" "${FG_GREEN}${BOLD}$*${RESET}"
}

warning() {
    printf "%b\n" "${FG_YELLOW}${BOLD}$*${RESET}"
}

error() {
    printf "%b\n" "${FG_RED}${BOLD}$*${RESET}"
}

info() {
    printf "%b\n" "${FG_BLUE}${BOLD}$*${RESET}"
}

normal() {
    printf "%b\n" "$*"
}

bold() {
    printf "%b\n" "${BOLD}$*${RESET}"
}

# --- (1) start --------------------------------------------------------------------------------------

start_heading "SERVER SETUP (UBUNTU)"

USERNAME=$(whoami)
success "Detected username as: $USERNAME"
# Exit if ran as root
if [ "$USERNAME" = "root" ]; then
    error "Whoa there! Please run this script as your normal user, NOT with sudo!"
    error "The script will ask for sudo password internally when needed."
    exit 1
fi

# --- (2) SYSTEM SETUP ---------------------------------------------------------------------------------
subheading "System Setup"
info "Updating package lists and upgrading system..."
sudo apt update && sudo apt upgrade -y

info "Installing essential packages..."
sudo apt install unattended-upgrades ufw htop git curl unzip python3-pip python3-venv libcairo2-dev pkg-config python3-dev gcc ffmpeg postgresql postgresql-contrib -y
sudo systemctl enable --now unattended-upgrades

# --- (3) DATABASE SETUP --------------------------------------------------------------------------------

subheading "Database Setup"

DB_NAME="tgbot"
DB_USER="tgbot_user"
DB_PASS=$(openssl rand -base64 24)

info "Creating database role..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<-EOSQL
    DO \$\$
    BEGIN
       IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
          CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
       ELSE
          ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';
       END IF;
    END
    \$\$;
EOSQL
success "Database user '"${DB_USER}"' created."

info "Creating database (if it doesn't exist)..."
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
else
    sudo -u postgres psql -c "ALTER DATABASE ${DB_NAME} OWNER TO ${DB_USER};"
fi

success "Database '${DB_NAME}' ready, owned by '${DB_USER}'."



info "Hardening PostgreSQL with Systemd Auto-Restart..."

# find the version number of postgresql
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | head -n 1 || true)

# Only proceed if a version was actually found.
if [ -n "$PG_VERSION" ]; then
    info "Found PostgreSQL version $PG_VERSION. Applying resilience configuration..."
    
    # Use the detected version to create the correct directory path.
    sudo mkdir -p "/etc/systemd/system/postgresql@${PG_VERSION}-main.service.d/"

    # Create the override config file in the dynamically-found path.
    sudo bash -c "cat << EOF > /etc/systemd/system/postgresql@${PG_VERSION}-main.service.d/override.conf
[Service]
Restart=always
RestartSec=5s
EOF"


    # Reload the systemd daemon to apply the new configuration.
    sudo systemctl daemon-reload
    
    # Restart the service to ensure the new policy is applied to the running process.
    info "Restarting PostgreSQL to apply the new resilience policy..."
    sudo systemctl restart "postgresql@${PG_VERSION}-main.service"

    success "PostgreSQL resilience configured for version $PG_VERSION."
else
    # If no running postgresql service was found, print a warning.
    error "Fatal Error: Could not detect running PostgreSQL service. This is a critical dependency."
    error "Aborting setup."
    exit 1
fi

info "Configuring PostgreSQL to allow local connections..."

PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
sudo cp "$PG_HBA" "${PG_HBA}.bak.$(date +%s)"

sudo bash -c "cat > '$PG_HBA'" <<'EOF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# "local" is for Unix domain socket connections only
local   all             all                                     peer
# IPv4 local connections:
host    all             all             127.0.0.1/32            scram-sha-256
# IPv6 local connections:
host    all             all             ::1/128                 scram-sha-256
# Allow replication connections from localhost, by a user with the
# replication privilege.
local   replication     all                                     scram-sha-256
host    replication     all             127.0.0.1/32            scram-sha-256
host    replication     all             ::1/128                 scram-sha-256
EOF

info "Restarting PostgreSQL to apply the new configuration..."
sudo systemctl restart "postgresql@${PG_VERSION}-main.service"

sudo systemctl enable --now postgresql@${PG_VERSION}-main
success "PostgreSQL enabled and started successfully!"

# Stop postgres auto-updates
info "Preventing PostgreSQL from automatically updating..."
# We create a new file specifically for our bot's database rules.
# Naming it starting with '99' ensures it is read last and appended safely
sudo bash -c 'cat << EOF > /etc/apt/apt.conf.d/99-postgres-blacklist
Unattended-Upgrade::Package-Blacklist {
    "postgresql";
};
EOF'
success "PostgreSQL updates safely blacklisted!"

# --- (4) LOGGING SETUP ---------------------------------------------------------------------------------
subheading "Logging Setup"
info "Installing pm2..."
sudo apt install -y nodejs npm
sudo npm install -g pm2

info "Installing pm2-logrotate..."
pm2 install pm2-logrotate

info "Configuring pm2-logrotate..."
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:max_size 100M
pm2 set pm2-logrotate:dateFormat 'YYYY-MM-DD_HH-mm-ss'
pm2 set pm2-logrotate:workerInterval 30

success "Set up pm2 and pm2 logrotate successfully!"

info "Configuring pm2 to survive reboots..."

STARTUP_OUTPUT=$(pm2 startup systemd -u "$USERNAME" --hp "$HOME")
STARTUP_CMD=$(echo "$STARTUP_OUTPUT" | grep -E '^sudo ')

if [ -n "$STARTUP_CMD" ]; then
    info "Running: $STARTUP_CMD"
    eval "$STARTUP_CMD"
    success "pm2 startup hook installed."
else
    warning "Couldn't auto-detect the pm2 startup command. Raw output was:"
    echo "$STARTUP_OUTPUT"
    warning "You may need to run 'pm2 startup' manually."
fi

# --- (5) REPO CLONING AND ENVIRONMENT SETUP -----------------------------------------------------------------------
GIT_REPO_URL="github.com/not-right-now/tg_to_wa_private.git"
REPO_DIR=$(basename "$GIT_REPO_URL" .git)

subheading "Repo Cloning and Environment Setup"
bold "Repo: $GIT_REPO_URL"
info "Repository will be cloned into: $REPO_DIR"
info "Cloning repository..."
git clone "$GIT_REPO_URL" "$REPO_DIR"

cd "$REPO_DIR"

info "Creating log directory..."
mkdir -p "storage/logs"

info "Writing database credentials..."
ENV_FILE="storage/.db_credentials"

python3 - "$DB_NAME" "$DB_USER" "$DB_PASS" "localhost" "5432" > "$ENV_FILE" <<'PYEOF'
import json, sys
keys = ["DB_NAME", "DB_USER", "DB_PASS", "DB_HOST", "DB_PORT"]
print(json.dumps(dict(zip(keys, sys.argv[1:])), indent=2))
PYEOF

chmod 600 "$ENV_FILE"
success "Credentials written to $ENV_FILE (chmod 600)"


info "Creating a python virtual environment..."
python3 -m venv venv
source venv/bin/activate

info "Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    python3 -m pip install -r requirements.txt
else
    warning "Warning: requirements.txt not found. Skipping dependency installation."
fi
deactivate


printf "%b" "${BOLD}Do you want to setup env variables now? [Y/n]: ${RESET}"
read -r SETUP_ENV_CHOICE || true
SETUP_ENV_CHOICE=${SETUP_ENV_CHOICE:-Y}

if [[ "$SETUP_ENV_CHOICE" =~ ^[Yy] ]]; then
    python3 scripts/env_setup.py
else
    warning "Skipping env setup. Run 'python3 scripts/env_setup.py' from the repo root before starting the bot!"
fi



finish_heading SETUP COMPLETE!

info Now use these commands to run the bot and make it run 24/7 if you have setup env variables otherwise set it up first!
info Use this command to start the bot
echo "pm2 start bot.config.js"
info Then use this command to save the session
echo "pm2 save"

echo -e "${BOLD}${FG_MAGENTA}---------------------------------------------------${RESET}"

info "(Optional) You may also configure the postgres \"listen_addresses\" if you want to accees the database form some other machine!"
echo "sudo nano /etc/postgresql/${PG_VERSION}/main/postgresql.conf"
bold If you want to allow other machines to access the database, then configure the pg_hba.conf file as well.
echo "sudo nano /etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
bold Then restart postgresql
echo "sudo systemctl restart postgresql@${PG_VERSION}-main.service"