# 🐘 PostgreSQL Server Setup Guide

Welcome! This guide provides a comprehensive walkthrough for installing and configuring a PostgreSQL database server on Linux. We'll cover everything from installation to creating your first user and database, ensuring it's secure and accessible for your applications.

## Table of Contents

1.  [Prerequisites](#-prerequisites)
2.  [Step 1: Install PostgreSQL](#-step-1-install-postgresql)
3.  [Step 2: Initialize & Start the Service](#-step-2-initialize--start-the-service)
4.  [Step 3: Configure Remote Access](#-step-3-configure-remote-access)
5.  [Step 4: Configure Client Authentication](#-step-4-configure-client-authentication)
6.  [Step 5: Configure the Firewall](#-step-5-configure-the-firewall)
7.  [Step 6: Create Your Database & User](#-step-6-create-your-database--user)
8.  [Step 7: Connect to Your New Database](#-step-7-connect-to-your-new-database)
9.  [Troubleshooting](#-troubleshooting)

---

### ✅ Prerequisites

Before you begin, make sure you have:
* A server running a modern Linux distribution.
* Access to a user account with `sudo` privileges.
* A text editor like `nano` or `vim`.

---

### 📦 Step 1: Install PostgreSQL

First things first, let's get the software installed. The command will vary depending on your Linux distribution. Pick the one that matches your system!

* **For Debian or Ubuntu:**
    ```bash
    sudo apt update
    sudo apt install postgresql postgresql-contrib
    ```

* **For Fedora, RHEL, CentOS, or AlmaLinux:**
    ```bash
    sudo dnf install postgresql-server postgresql-contrib
    ```

* **For Arch Linux:**
    ```bash
    sudo pacman -S postgresql
    ```

---

### 🚀 Step 2: Initialize & Start the Service

Out of the box, the database cluster (the collection of databases managed by the server) needs to be initialized.

* **For Fedora, RHEL, CentOS, or AlmaLinux:**
    ```bash
    # This command sets up the initial database structure. Only run this once!
    sudo postgresql-setup --initdb
    ```

* **For Debian or Ubuntu:**
    This is usually handled automatically for you during the installation process. You can skip this step!

* **For Arch Linux:**
    You need to initialize it manually.
    ```bash
    # Initialize the database in the default location.
    sudo -iu postgres initdb -D /var/lib/postgres/data
    ```

**Now, let's start the database service and enable it to launch on boot:**

_As discussed above, this is not required for Debian or Ubuntu._
```bash
# Start the PostgreSQL service right now
sudo systemctl start postgresql

# Enable it to start automatically every time the server boots up
sudo systemctl enable postgresql
```

You can check its status with `sudo systemctl status postgresql` or `sudo systemctl status postgresql@16-main` (Debian/Ubuntu). You should see it's `active (running)`.

---

### 📡 Step 3: Configure Remote Access

By default, PostgreSQL only listens for connections from the local machine. To allow remote connections, you need to tell it which network addresses to listen on.

1.  **Open the main configuration file:**
    The path can vary, but it's often at `/var/lib/pgsql/data/postgresql.conf` (Fedora/RHEL) or `/etc/postgresql/<version>/main/postgresql.conf` (Debian/Ubuntu).
    
    For Debian or Ubuntu:
    ```bash
    sudo nano /etc/postgresql/16/main/postgresql.conf
    ```
    For Fedora, RHEL, CentOS, or AlmaLinux:
    ```bash
    sudo nano /var/lib/pgsql/data/postgresql.conf
    ```

3.  **Find the `listen_addresses` setting:**
    Scroll down until you find the `#listen_addresses = 'localhost'` line.

4.  **Change it!**
    * **For security, it's best to specify the IP addresses** that will be connecting. This creates a smaller attack surface.
        ```conf
        # Example with a list of allowed IPs
        listen_addresses = 'localhost,192.168.1.100'
        ```
    * If you need to allow connections from any IP address (use with caution!), you can use `*`.
        ```conf
        # This is less secure but useful for dynamic IPs or public services.
        listen_addresses = '*'
        ```

---

### 🔐 Step 4: Configure Client Authentication

This is the most critical security step. The `pg_hba.conf` (Host-Based Authentication) file acts as the database's security guard, defining who can connect, from where, and how they must prove their identity.

1.  **Open the `pg_hba.conf` file:**
    It's in the same directory as `postgresql.conf`.
    
    Debian or Ubuntu:
    ```bash
    sudo nano /etc/postgresql/16/main/pg_hba.conf
    ```
    For Fedora, RHEL, CentOS, or AlmaLinux:
    ```bash
    sudo nano /var/lib/pgsql/data/pg_hba.conf
    ```

3.  **Understand the Default Rules:**
    Near the bottom, you'll see lines like this:
    ```conf
    # TYPE  DATABASE        USER            ADDRESS                 METHOD
    local   all             all                                     peer
    host    all             all             127.0.0.1/32            ident # or peer
    host    all             all             ::1/128                 ident # or peer
    ```
    * **`peer` and `ident`** are methods that rely on the operating system's username. They are secure for local access but don't work for remote password-based connections.
    * **`scram-sha-256`** is a modern, secure password-based authentication method. This is what we want for our remote users.

4.  **Update the Authentication Methods:**
    Change the `METHOD` for local and network connections to `scram-sha-256`. We'll also add a rule to let the `postgres` superuser connect locally without a password for easy administration.

    **Your new rules should look like this:**
    ```conf
    # TYPE  DATABASE        USER            ADDRESS                 METHOD

    # Allow postgres user to connect locally using peer auth for admin tasks
    local   all             postgres                                peer

    # "local" is for Unix domain socket connections only, now requires a password
    local   all             all                                     scram-sha-256
    # IPv4 local connections, now requires a password
    host    all             all             127.0.0.1/32            scram-sha-256
    # IPv6 local connections, now requires a password
    host    all             all             ::1/128                 scram-sha-256

    # --- ADD YOUR REMOTE CONNECTION RULES BELOW ---
    # Example: Allow 'bot_user' to connect to 'bot_db' from a specific IP
    host    bot_db         bot_user         203.0.113.42/32         scram-sha-256

    # Example: Allow 'bot_user' to connect to 'bot_db' from a whole subnet
    host    bot_db         bot_user         198.51.100.0/24         scram-sha-256
    ```

5.  **Restart PostgreSQL to Apply Changes:**
    This is crucial! The server won't see your new rules until you restart it.

    For for Debian or Ubuntu:
    ```bash
    sudo systemctl restart postgresql@16-main
    ```
    For Fedora, RHEL, CentOS, or AlmaLinux:
    ```bash
    sudo systemctl restart postgresql
    ```

---

### 🔥 Step 5: Configure the Firewall

You've told PostgreSQL to listen for remote connections, but the server's firewall will block them by default. We need to open the port for PostgreSQL (port `5432`).

* **For `ufw` (Debian, Ubuntu):**
    ```bash
    # Allow traffic on the default PostgreSQL port.
    sudo ufw allow 5432/tcp

    # You might see this work as well
    # sudo ufw allow postgresql

    # Make sure to enable ufw if it isn't already
    sudo ufw enable
    ```

* **For `firewalld` (Fedora, RHEL, CentOS):**
    ```bash
    # Add the postgresql service to the allowed list, permanently.
    sudo firewall-cmd --add-service=postgresql --permanent

    # Reload the firewall to apply the new rule.
    sudo firewall-cmd --reload
    ```

---

### 🤖 Step 6: Create Your Database & User

Now for the fun part! Let's create a dedicated user and a database for your application.

1.  **Switch to the `postgres` system user.** This user is the superuser for the database.
    ```bash
    sudo -i -u postgres
    ```

2.  **Open the PostgreSQL command-line prompt (`psql`).**
    ```bash
    psql
    ```

3.  **Run the following SQL commands.**
    * Create a new user (a "role") with a password. **Remember to change `'change_me'` to a strong, secure password!**
        ```sql
        CREATE ROLE bot_user WITH LOGIN PASSWORD 'change_me';
        ```
    * Create the database and assign the new user as its owner.
        ```sql
        CREATE DATABASE bot_db OWNER bot_user;
        ```
    * Connect to your new database to manage its permissions.
        ```sql
        \c bot_db
        ```
    * Grant your new user all privileges on the `public` schema within this database.
        ```sql
        GRANT ALL ON SCHEMA public TO bot_user;
        ```

4.  **Exit `psql` and return to your normal user account.**
    ```sql
    -- Exit the psql prompt
    \q
    ```
    ```bash
    # Exit the postgres user session
    exit
    ```

---

### 🔌 Step 7: Connect to Your New Database

Let's test it out!

* **From the local machine:**
    ```bash
    psql -U bot_user -d bot_db -h localhost
    ```
    It will prompt you for the password you set.

* **From a remote machine** (that you allowed in `pg_hba.conf` and the firewall):
    ```bash
    psql -U bot_user -d bot_db -h <your_server_ip_address>
    ```

You're in! 🎉

---

### 🤔 Troubleshooting

* **Connection Refused:**
    1.  Check that `listen_addresses` in `postgresql.conf` is correct.
    2.  Ensure your firewall (`firewalld` or `ufw`) is configured to allow port `5432`.
    3.  Confirm the PostgreSQL service is running: `sudo systemctl status postgresql`.

* **FATAL: password authentication failed for user "..."**
    1.  You entered the wrong password.
    2.  Check `pg_hba.conf` and make sure the `METHOD` for that connection type is `scram-sha-256`, not `peer` or `ident`.

* **FATAL: Peer authentication failed for user "..."**
  This means `pg_hba.conf` has a `peer` rule for this connection. `peer` requires that your Linux username matches the database username you are trying to connect with. For remote connections, you almost always want `scram-sha-256` instead.


