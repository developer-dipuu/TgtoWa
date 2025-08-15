
import sqlite3
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from config import OWNER_ID, DATA_DIR

# Ensure the data directory exists before defining the DB file path
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "bot_data.db")

logger = logging.getLogger(__name__)

def get_db_connection():
    """Establishes a connection to the database."""
    conn = sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes the database and creates tables if they don't exist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # For logging all unique users who start the bot
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_seen TIMESTAMP NOT NULL
                )
            """)

            # Table for user stats
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    total_requests INTEGER DEFAULT 0,
                    succeeded_requests INTEGER DEFAULT 0,
                    failed_requests INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # For logging every single conversion request
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversion_log (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    pack_url TEXT NOT NULL,
                    is_emoji BOOLEAN NOT NULL,
                    status TEXT NOT NULL,
                    request_time TIMESTAMP NOT NULL,
                    completion_time TIMESTAMP,
                    duration_seconds REAL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # For storing current admins
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    promoted_by INTEGER NOT NULL,
                    promotion_date TIMESTAMP NOT NULL
                )
            """)

            # For storing premium users and their expiration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS premium_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    added_by INTEGER NOT NULL,
                    start_date TIMESTAMP NOT NULL,
                    expiry_date TIMESTAMP NOT NULL
                )
            """)

            # admin_history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL
                )
            """)
            
            # premium_history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS premium_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    duration_change_days INTEGER,
                    previous_expiry_date TIMESTAMP,
                    new_expiry_date TIMESTAMP,
                    timestamp TIMESTAMP NOT NULL
                )
            """)

            # Table for currently banned users
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY,
                    banned_by_admin_id INTEGER NOT NULL,
                    ban_date TIMESTAMP NOT NULL,
                    is_silent BOOLEAN NOT NULL,
                    reason TEXT
                )
            """)

            # Table for logging all ban/unban actions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ban_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_user_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    action TEXT NOT NULL, -- 'banned' or 'unbanned'
                    is_silent_ban BOOLEAN, -- TRUE for /sban, FALSE for /ban, NULL for unban
                    reason TEXT,
                    timestamp TIMESTAMP NOT NULL
                )
            """)

            # For the user's initial contact message
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    user_message_id INTEGER NOT NULL,
                    user_message_text TEXT, -- Storing the actual message content
                    timestamp_sent TIMESTAMP NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' or 'replied'
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # For logging all admin replies
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_replies (
                    reply_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    admin_reply_message_id INTEGER NOT NULL,
                    admin_reply_text TEXT, -- Storing the actual reply content
                    timestamp_replied TIMESTAMP NOT NULL,
                    FOREIGN KEY (contact_id) REFERENCES contact_messages (contact_id),
                    FOREIGN KEY (admin_id) REFERENCES admins (user_id)
                )
            """)

            # For logging all broadcast messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_log (
                    broadcast_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    message_content TEXT,
                    flags TEXT,
                    total_users INTEGER,
                    success_count INTEGER,
                    fail_count INTEGER,
                    is_forward BOOLEAN DEFAULT FALSE,
                    forwarded_from_id INTEGER,
                    forwarded_message_id INTEGER,
                    FOREIGN KEY (admin_id) REFERENCES users (user_id)
                )
            """)

            # For logging all /send messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS send_log (
                    send_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    message_content TEXT,
                    flags TEXT,
                    target_users_list TEXT,
                    total_users INTEGER,
                    success_count INTEGER,
                    fail_count INTEGER,
                    is_forward BOOLEAN DEFAULT FALSE,
                    forwarded_from_id INTEGER,
                    forwarded_message_id INTEGER,
                    FOREIGN KEY (admin_id) REFERENCES users (user_id)
                )
            """)
            
            # sticker set stats 
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sticker_set_stats (
                    set_id INTEGER PRIMARY KEY,
                    short_name TEXT UNIQUE NOT NULL,
                    is_emoji BOOLEAN NOT NULL,
                    pack_title TEXT,
                    sticker_count INTEGER,
                    request_count INTEGER DEFAULT 1,
                    last_conversion_duration REAL,
                    cache_score REAL DEFAULT 0.0,
                    last_updated TIMESTAMP NOT NULL
                )
            """)

            # For tracking which packs are currently cached
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cached_packs (
                    set_id INTEGER PRIMARY KEY,
                    cache_score REAL NOT NULL,
                    cached_at TIMESTAMP NOT NULL,
                    files_path TEXT NOT NULL,
                    FOREIGN KEY (set_id) REFERENCES sticker_set_stats (set_id) ON DELETE CASCADE
                )
            """)

            # For storing pre-calculated popular packs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS popular_packs (
                    list_type TEXT NOT NULL, -- 'daily' or 'all_time'
                    rank INTEGER NOT NULL,
                    pack_title TEXT NOT NULL,
                    pack_url TEXT NOT NULL,
                    last_updated TIMESTAMP NOT NULL,
                    PRIMARY KEY (list_type, rank)
                )
            """)

            # ---- Indexes for fast lookups ----

            # For faster cache score lookups (for replacing cache and all)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cached_packs_score ON cached_packs (cache_score)
            """)
            
            # Speeds up the daily premium user cleanup job and /gstats list
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_premium_users_expiry_date ON premium_users (expiry_date)
            """)
            # Speeds up /refreshcache by quickly sorting all packs
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sticker_set_stats_cache_score ON sticker_set_stats (cache_score)
            """)
            # Speeds up the daily stats calculation for /gstats (this is must as the conversion_log will get damn large)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversion_log_request_time ON conversion_log (request_time)
            """)
            # Speeds up fetching admin reply history
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_admin_replies_contact_id ON admin_replies (contact_id)
            """)
            # Speeds up fetching the top users list in /gstats (i do this damn often so bettr have this)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_stats_total_requests ON user_stats (total_requests)
            """)


            conn.commit()
            logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database error during initialization: {e}", exc_info=True)



# User Logging
def add_or_update_user(user_id: int, username: Optional[str], full_name: str):
    """Adds a new user or updates their details if they already exist."""
    safe_full_name = full_name[:129]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cursor.fetchone() is None:
            # New user
            cursor.execute(
                "INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
                (user_id, username, datetime.now().replace(microsecond=0))
            )
            # Initialize their stats
            cursor.execute(
                "INSERT INTO user_stats (user_id, username, full_name) VALUES (?, ?, ?)",
                (user_id, username, safe_full_name)
            )
            logger.info(f"New user recorded: {safe_full_name} ({user_id})")
        else:
            # Existing user, update details
            cursor.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (username, user_id)
            )
            # Update their stats details as well
            cursor.execute(
                "UPDATE user_stats SET username = ?, full_name = ? WHERE user_id = ?",
                (username, safe_full_name, user_id)
            )
        conn.commit()

def get_user_stats(user_id: int) -> dict:
    """Gets conversion stats for a specific user from the optimized table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT total_requests, succeeded_requests, failed_requests FROM user_stats WHERE user_id = ?", (user_id,))
        stats = cursor.fetchone()
        if stats:
            return {"total": stats['total_requests'], "succeeded": stats['succeeded_requests'], "failed": stats['failed_requests']}
    return {"total": 0, "succeeded": 0, "failed": 0}

# fetching all user ids from database
def get_all_user_ids() -> list[int]:
    """Retrieves all user IDs from the database for broadcasting."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        # fetchall returns a list of tuples like [(123,), (456,)]
        return [row['user_id'] for row in cursor.fetchall()]
    
# fetch all admins including owner from the database
def get_all_admin_ids() -> List[int]:
    """Retrieves all admin and owner IDs from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        admin_ids = [row['user_id'] for row in cursor.fetchall()]
        # Ensure owner is always included and the list is unique
        if OWNER_ID not in admin_ids:
            admin_ids.append(OWNER_ID)
        return admin_ids

# for the owner's /gstats command
def get_gstats() -> dict:
    """Gathers all global statistics for the /gstats command."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM admins")
        total_admins = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM banned_users")
        total_banned = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM premium_users WHERE expiry_date > ?", (datetime.now().replace(microsecond=0),))
        active_premium = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(succeeded_requests), SUM(failed_requests) FROM user_stats")
        total_succeeded, total_failed = cursor.fetchone()
        
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute("SELECT status, COUNT(*) FROM conversion_log WHERE request_time >= ? GROUP BY status", (today_start,))
        today_stats_raw = cursor.fetchall()
        today_succeeded = sum(row[1] for row in today_stats_raw if row[0].startswith('completed'))
        today_failed = sum(row[1] for row in today_stats_raw if row[0].startswith('failed'))
        
        return {
            "total_users": total_users,
            "total_admins": total_admins,
            "total_banned": total_banned,
            "active_premium": active_premium,
            "total_succeeded": total_succeeded or 0,
            "total_failed": total_failed or 0,
            "today_succeeded": today_succeeded,
            "today_failed": today_failed
        }

def get_gstats_premium_list() -> list:
    """Gets a list of all active premium users for /gstats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username, expiry_date FROM premium_users WHERE expiry_date > ? ORDER BY expiry_date ASC",
            (datetime.now().replace(microsecond=0),)
        )
        return cursor.fetchall()

def get_gstats_top_users(limit: int = 50) -> list:
    """Gets a list of top users by total requests."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, full_name, total_requests FROM user_stats ORDER BY total_requests DESC LIMIT ?",
            (limit,)
        )
        return cursor.fetchall()

def get_gstats_admins_list() -> list:
    """Gets a list of all admins."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM admins")
        return cursor.fetchall()

def get_gstats_banned_list() -> list:
    """Gets a list of all banned users."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, reason, ban_date FROM banned_users ORDER BY ban_date DESC")
        return cursor.fetchall()
    
    
# Conversion Logging
def log_conversion_request(user_id: int, pack_url: str, is_emoji: bool) -> int:
    """Logs the start of a conversion request and returns the log ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversion_log (user_id, pack_url, is_emoji, status, request_time) VALUES (?, ?, ?, ?, ?)",
            (user_id, pack_url, is_emoji, "processing", datetime.now().replace(microsecond=0))
        )
        conn.commit()
        return cursor.lastrowid

def update_conversion_log(log_id: int, status: str, completion_time: datetime, duration: float):
    """Updates a conversion log entry and the user's stats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # First update the detailed log
        cursor.execute(
            "UPDATE conversion_log SET status = ?, completion_time = ?, duration_seconds = ? WHERE log_id = ?",
            (status, completion_time.replace(microsecond=0), round(duration, 2), log_id)
        )
        
        # Update the stats table
        # Get the user_id for this conversion
        cursor.execute("SELECT user_id FROM conversion_log WHERE log_id = ?", (log_id,))
        result = cursor.fetchone()
        if result:
            user_id = result['user_id']
            # Increment the correct counter
            if status.startswith("completed"):
                cursor.execute("UPDATE user_stats SET succeeded_requests = succeeded_requests + 1 WHERE user_id = ?", (user_id,))
            else: # "failed"
                cursor.execute("UPDATE user_stats SET failed_requests = failed_requests + 1 WHERE user_id = ?", (user_id,))
            # Always increment total requests
            cursor.execute("UPDATE user_stats SET total_requests = total_requests + 1 WHERE user_id = ?", (user_id,))

        conn.commit()


########### Role Checks (Owner, Admin, Premium) ###########
def is_owner(user_id: int) -> bool:
    """Checks if a user is the owner."""
    return user_id == OWNER_ID

def is_admin(user_id: int) -> bool:
    """Checks if a user is an admin or the owner."""
    if is_owner(user_id):
        return True
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def is_premium(user_id: int) -> bool:
    """Checks if a user has an active premium subscription."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT expiry_date FROM premium_users WHERE user_id = ? AND expiry_date > ?",
            (user_id, datetime.now().replace(microsecond=0))
        )
        return cursor.fetchone() is not None

#################  Admin Management ###########
def add_admin(user_id: int, username: str, promoted_by: int):
    """Promotes a user to admin."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO admins (user_id, username, promoted_by, promotion_date) VALUES (?, ?, ?, ?)",
            (user_id, username, promoted_by, datetime.now().replace(microsecond=0))
        )
        conn.execute(
            "INSERT INTO admin_history (admin_id, target_user_id, action, timestamp) VALUES (?, ?, ?, ?)",
            (promoted_by, user_id, 'promoted', datetime.now().replace(microsecond=0))
        )
        conn.commit()

def remove_admin(user_id: int, demoted_by: int) -> bool:
    """Demotes an admin."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        if cursor.rowcount > 0:
            conn.execute(
                "INSERT INTO admin_history (admin_id, target_user_id, action, timestamp) VALUES (?, ?, ?, ?)",
                (demoted_by, user_id, 'demoted', datetime.now().replace(microsecond=0))
            )
            conn.commit()
            return True
        return False

############# Premium User Management ############

def add_premium(user_id: int, username: str, duration_days: int, added_by: int):
    """Adds or extends a user's premium subscription."""
    now = datetime.now().replace(microsecond=0)
    expiry_date = now + timedelta(days=duration_days)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO premium_users (user_id, username, added_by, start_date, expiry_date) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, added_by, now, expiry_date)
        )
        # Log to history with all details
        conn.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (added_by, user_id, 'added', duration_days, None, expiry_date, now)
        )
        conn.commit()

def remove_premium(user_id: int, admin_id: int) -> bool:
    """Removes a user's premium subscription."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Get the current expiry date BEFORE deleting
        cursor.execute("SELECT expiry_date FROM premium_users WHERE user_id = ?", (user_id,))
        current_premium = cursor.fetchone()
        previous_expiry = current_premium['expiry_date'] if current_premium else None

        cursor.execute("DELETE FROM premium_users WHERE user_id = ?", (user_id,))
        if cursor.rowcount > 0:
            # Log the removal action
            conn.execute(
                "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (admin_id, user_id, 'removed', None, previous_expiry, None, datetime.now().replace(microsecond=0))
            )
            conn.commit()
            return True
        return False
    
def get_premium_duration_left(user_id: int) -> Optional[timedelta]:
    """
    Calculates the remaining duration for a premium user's subscription.
    Returns a timedelta object if the subscription is active, otherwise None.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT expiry_date FROM premium_users WHERE user_id = ? AND expiry_date > ?",
            (user_id, datetime.now().replace(microsecond=0))
        )
        result = cursor.fetchone()
        if result:
            expiry_date = result['expiry_date']
            return expiry_date - datetime.now().replace(microsecond=0)
    return None

def manage_premium_duration(user_id: int, days: int, admin_id: int, action: str) -> Optional[datetime]:
    """Extends or deducts days from a premium subscription. 'days' can be negative for deduction."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT expiry_date FROM premium_users WHERE user_id = ?", (user_id,))
        current_premium = cursor.fetchone()

        if not current_premium:
            return None # User is not premium, cannot extend/deduct

        current_expiry = current_premium['expiry_date']
        new_expiry = current_expiry + timedelta(days=days)

        # Update the expiry date
        cursor.execute("UPDATE premium_users SET expiry_date = ? WHERE user_id = ?", (new_expiry, user_id))

        # Log this action to history
        conn.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (admin_id, user_id, action, days, current_expiry, new_expiry, datetime.now().replace(microsecond=0))
        )
        conn.commit()

        return new_expiry

def remove_expired_premium_users() -> int:
    """
    Finds and removes premium users whose subscriptions have expired.
    Logs the removal to the premium_history table as an 'expired' action.
    Returns the number of users removed.
    """
    now = datetime.now().replace(microsecond=0)
    system_admin_id = 0 # Using 0 as a special ID for automated system actions

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # First, find all users whose subscriptions have expired to log them
        cursor.execute(
            "SELECT user_id, expiry_date FROM premium_users WHERE expiry_date <= ?",
            (now,)
        )
        expired_users = cursor.fetchall()

        if not expired_users:
            return 0 # No one to remove

        # Log that these users expired before we delete them
        history_logs = []
        for user in expired_users:
            # We log the action as 'expired' by the SYSTEM (admin_id=0)
            history_logs.append(
                (system_admin_id, user['user_id'], 'expired', None, user['expiry_date'], None, now)
            )
        
        cursor.executemany(
            """
            INSERT INTO premium_history 
            (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, timestamp) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            history_logs
        )

        # Now delete the expired users from the main premium table
        cursor.execute("DELETE FROM premium_users WHERE expiry_date <= ?", (now,))
        
        removed_count = cursor.rowcount
        conn.commit()
        
        return removed_count

########## Ban Management ###########

def is_banned(user_id: int) -> bool:
    """Checks if a user is in the banned table. This is the fast check."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def ban_user(user_id: int, admin_id: int, reason: Optional[str], is_silent: bool):
    """Adds a user to the banned_users table and logs it to history."""
    now = datetime.now().replace(microsecond=0)
    with get_db_connection() as conn:
        # Add to currently banned table
        conn.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, banned_by_admin_id, ban_date, is_silent, reason) VALUES (?, ?, ?, ?, ?)",
            (user_id, admin_id, now, is_silent, reason)
        )
        # Add to history log
        conn.execute(
            "INSERT INTO ban_history (target_user_id, admin_id, action, is_silent_ban, reason, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, admin_id, 'banned', is_silent, reason, now)
        )
        conn.commit()

def unban_user(user_id: int, admin_id: int, reason: Optional[str]) -> bool:
    """Removes a user from the banned_users table and logs it to history."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        
        # Only log if a user was actually removed
        if cursor.rowcount > 0:
            conn.execute(
                "INSERT INTO ban_history (target_user_id, admin_id, action, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                (user_id, admin_id, 'unbanned', reason, datetime.now().replace(microsecond=0))
            )
            conn.commit()
            return True
        return False

############ Contact Logging #################

def log_contact_message(user_id: int, user_message_id: int, message_text: str) -> int:
    """Logs a new contact message from a user into the contact_messages table and returns contact_id"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO contact_messages (user_id, user_message_id, user_message_text, timestamp_sent) VALUES (?, ?, ?, ?)",
            (user_id, user_message_id, message_text, datetime.now().replace(microsecond=0))
        )
        conn.commit()
        logger.info(f"Logged new contact message from user {user_id}. contact_id: {cursor.lastrowid}")
        return cursor.lastrowid

def log_admin_reply(contact_id: int, admin_id: int, admin_reply_message_id: int, reply_text: str):
    """Logs an admin's reply and updates the original message status."""
    with get_db_connection() as conn:
        # Add the new reply to the replies table
        conn.execute(
            "INSERT INTO admin_replies (contact_id, admin_id, admin_reply_message_id, admin_reply_text, timestamp_replied) VALUES (?, ?, ?, ?, ?)",
            (contact_id, admin_id, admin_reply_message_id, reply_text, datetime.now().replace(microsecond=0))
        )
        # Mark the original message as 'replied'
        conn.execute(
            "UPDATE contact_messages SET status = 'replied' WHERE contact_id = ?",
            (contact_id,)
        )
        conn.commit()
        logger.info(f"Logged admin reply for contact_id {contact_id} by admin {admin_id}")

def get_previous_replies(contact_id: int) -> List[sqlite3.Row]:
    """Checks for and returns any previous replies for a given contact_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admin_replies WHERE contact_id = ? ORDER BY timestamp_replied ASC", (contact_id,))
        return cursor.fetchall()

def get_contact_details(contact_id: int) -> Optional[dict]:
    """
    Fetches full details for a contact ticket, including the user's info,
    the original message, and all admin replies with admin info.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Get the original message and join with user_stats to get the user's name
        query = """
            SELECT cm.*, us.full_name as user_full_name
            FROM contact_messages cm
            LEFT JOIN user_stats us ON cm.user_id = us.user_id
            WHERE cm.contact_id = ?
        """
        cursor.execute(query, (contact_id,))
        contact_message = cursor.fetchone()

        if not contact_message:
            return None

        # Get all admin replies and join with user_stats to get admin names
        query = """
            SELECT ar.*, us.full_name as admin_full_name
            FROM admin_replies ar
            LEFT JOIN user_stats us ON ar.admin_id = us.user_id
            WHERE ar.contact_id = ?
            ORDER BY ar.timestamp_replied ASC
        """
        cursor.execute(query, (contact_id,))
        replies = cursor.fetchall()

        return {"user_message": contact_message, "admin_replies": replies}

############ Broadcast/send Logging #####################

def log_broadcast(admin_id: int, message_content: str, flags: str,
                  total_users: int, success_count: int, fail_count: int,
                  is_forward: bool = False, forwarded_from_id: Optional[int] = None,
                  forwarded_message_id: Optional[int] = None):
    """Logs a broadcast event to the database."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO broadcast_log (
                admin_id, timestamp, message_content, flags, total_users,
                success_count, fail_count, is_forward, forwarded_from_id,
                forwarded_message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (admin_id, datetime.now().replace(microsecond=0), message_content, flags, total_users,
             success_count, fail_count, is_forward, forwarded_from_id,
             forwarded_message_id)
        )
        conn.commit()
        logger.info(f"Logged broadcast from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")


def log_send(admin_id: int, message_content: str, flags: str,
             target_users_list: List[int], success_count: int, fail_count: int,
             is_forward: bool = False, forwarded_from_id: Optional[int] = None,
             forwarded_message_id: Optional[int] = None):
    """Logs a /send event to the database."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO send_log (
                admin_id, timestamp, message_content, flags, target_users_list,
                total_users, success_count, fail_count, is_forward, forwarded_from_id,
                forwarded_message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (admin_id, datetime.now().replace(microsecond=0), message_content, flags, json.dumps(target_users_list),
             len(target_users_list), success_count, fail_count, is_forward, forwarded_from_id,
             forwarded_message_id)
        )
        conn.commit()
        logger.info(f"Logged /send from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")


############### Sticker Pack Stats & Caching #################

def add_or_update_sticker_set_stats(set_id: int, short_name: str, is_emoji: bool, pack_title: str, sticker_count: int, conversion_duration: float, is_system_process: bool) -> float:
    """
    Adds or updates a sticker pack's stats after a conversion.
    Calculates and returns the new cache score.
    """
    from config import CACHE_SCORE_TIME_WEIGHT, CACHE_SCORE_REQUEST_WEIGHT

    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check current request count to calculate new score
        cursor.execute("SELECT request_count FROM sticker_set_stats WHERE set_id = ?", (set_id,))
        stat = cursor.fetchone()
        
        current_request_count = stat['request_count'] if stat else 0
        new_request_count = current_request_count if is_system_process else current_request_count + 1
        # Calculate the cache score
        cache_score = (CACHE_SCORE_TIME_WEIGHT * conversion_duration) + (CACHE_SCORE_REQUEST_WEIGHT * new_request_count)
        
        # Use INSERT ON CONFLICT to either create a new record or update an existing one
        cursor.execute(f"""
            INSERT INTO sticker_set_stats (set_id, short_name, is_emoji, pack_title, sticker_count, last_conversion_duration, cache_score, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(set_id) DO UPDATE SET
                pack_title = excluded.pack_title,
                short_name = excluded.short_name,
                sticker_count = excluded.sticker_count,
                request_count = CASE WHEN ? THEN request_count ELSE request_count + 1 END,
                last_conversion_duration = excluded.last_conversion_duration,
                cache_score = ?,
                last_updated = excluded.last_updated
        """, (set_id, short_name, is_emoji, pack_title, sticker_count, round(conversion_duration, 2),
               round(cache_score, 2), datetime.now().replace(microsecond=0), is_system_process, round(cache_score, 2)))
        
        conn.commit()
        
        logger.info(f"Updated stats for pack {short_name} (ID: {set_id}). New score: {cache_score:.2f}")
        return cache_score

def is_pack_cached(set_id: int, current_title: str, current_sticker_count: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Checks if a pack is cached and if the cache is up-to-date.

    Args:
        set_id: The ID of the sticker set.
        current_title: The current title of the pack (from Telegram).
        current_sticker_count: The current number of stickers in the pack (from Telegram).

    Returns:
        A tuple (status, data):
        - ('hit', '/path/to/files') if the cache is valid.
        - ('stale', '/path/to/files') if the cache exists but is outdated.
        - ('miss', None) if the pack is not in the cache.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Step 1: Check if the set_id even exists in our cache table.
        cursor.execute("SELECT files_path FROM cached_packs WHERE set_id = ?", (set_id,))
        cache_result = cursor.fetchone()

        if not cache_result:
            return 'miss', None  # It's not in the cache at all.

        files_path = cache_result['files_path']

        # Step 2: Since it's in the cache, let's check if it's stale by comparing stats.
        cursor.execute("SELECT pack_title, sticker_count FROM sticker_set_stats WHERE set_id = ?", (set_id,))
        stats_result = cursor.fetchone()

        if not stats_result or \
           stats_result['pack_title'] != current_title or \
           stats_result['sticker_count'] != current_sticker_count:
            # The stats don't match! The cached version is stale.
            logger.warning(f"Stale cache detected for pack {set_id}. Title or sticker count has changed.")
            return 'stale', files_path

        # Step 3: It's in the cache and the stats match. It's a valid hit!
        # As a bonus, we'll update its stats to reflect this new request, keeping it relevant.
        cursor.execute("SELECT short_name, is_emoji, last_conversion_duration FROM sticker_set_stats WHERE set_id = ?", (set_id,))
        pack_info = cursor.fetchone()
        if pack_info:
            add_or_update_sticker_set_stats(
                set_id=set_id,
                short_name=pack_info['short_name'],
                is_emoji=pack_info['is_emoji'],
                pack_title=current_title,
                sticker_count=current_sticker_count,
                conversion_duration=pack_info['last_conversion_duration'],
                is_system_process=False
            )
        logger.info(f"Cache hit for pack {set_id} at '{files_path}'.")
        return 'hit', files_path

def get_cache_info() -> Tuple[int, Optional[sqlite3.Row]]:
    """
    Gets the current number of items in the cache and the item with the lowest score.
    Returns a tuple: (current_cache_size, lowest_score_item_row).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cached_packs")
        count = cursor.fetchone()[0]
        
        lowest_item = None
        if count > 0:
            cursor.execute("SELECT * FROM cached_packs ORDER BY cache_score ASC LIMIT 1")
            lowest_item = cursor.fetchone()
            
        return count, lowest_item

def add_to_cache(set_id: int, cache_score: float, files_path: str):
    """Adds a pack to the cache tracking table."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cached_packs (set_id, cache_score, cached_at, files_path) VALUES (?, ?, ?, ?)",
            (set_id, round(cache_score, 2), datetime.now().replace(microsecond=0), files_path)
        )
        conn.commit()
        logger.info(f"Added pack {set_id} to cache with score {cache_score:.2f}")

def remove_from_cache(set_id: int):
    """Removes a pack from the cache tracking table."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM cached_packs WHERE set_id = ?", (set_id,))
        conn.commit()
        logger.info(f"Removed pack {set_id} from cache.")

def get_all_cached_packs() -> List[sqlite3.Row]:
    """Gets a list of all currently cached packs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT set_id, files_path FROM cached_packs")
        return cursor.fetchall()

def get_set_id_by_short_name(short_name: str) -> Optional[int]:
    """Finds a sticker set's ID by its short name in sticker_set_stats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT set_id FROM sticker_set_stats WHERE short_name = ?", (short_name,))
        result = cursor.fetchone()
        return result['set_id'] if result else None

def get_cached_pack_by_id(set_id: int) -> Optional[sqlite3.Row]:
    """Gets the file path for a single cached pack by its set_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT files_path FROM cached_packs WHERE set_id = ?", (set_id,))
        return cursor.fetchone()
    
def get_top_packs_by_score(limit: int) -> List[sqlite3.Row]:
    """Gets the top N sticker packs ordered by their cache score."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT short_name FROM sticker_set_stats ORDER BY cache_score DESC LIMIT ?",
            (limit,)
        )
        #returns a list of shortname strings
        return [row['short_name'] for row in cursor.fetchall()]

def calculate_and_store_popular_packs():
    """
    Calculates the top 10 daily and top 50 all-time packs and stores them.
    This is intended to be run once daily.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Start fresh
            cursor.execute("DELETE FROM popular_packs")

            # --- Calculate Daily Top 10 (from yesterday's data) ---
            now = datetime.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_start = today_start - timedelta(days=1)
            
            # This query joins conversion logs with pack stats to get titles
            daily_query = """
                SELECT
                    sss.pack_title,
                    cl.pack_url,
                    COUNT(cl.pack_url) as request_count
                FROM conversion_log cl
                JOIN sticker_set_stats sss ON cl.pack_url LIKE '%' || sss.short_name
                WHERE cl.request_time >= ? AND cl.request_time < ?
                GROUP BY cl.pack_url, sss.pack_title
                ORDER BY request_count DESC
                LIMIT 10;
            """
            cursor.execute(daily_query, (yesterday_start, today_start))
            daily_packs = cursor.fetchall()

            daily_inserts = []
            for i, pack in enumerate(daily_packs, 1):
                daily_inserts.append(('daily', i, pack['pack_title'], pack['pack_url'], now))
            
            cursor.executemany(
                "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES (?, ?, ?, ?, ?)",
                daily_inserts
            )

            # --- Calculate All-Time Top 50 ---
            all_time_query = """
                SELECT pack_title, short_name, is_emoji FROM sticker_set_stats ORDER BY request_count DESC LIMIT 50
            """
            cursor.execute(all_time_query)
            all_time_packs = cursor.fetchall()
            
            all_time_inserts = []
            for i, pack in enumerate(all_time_packs, 1):
                pack_type_url = "addemoji" if pack['is_emoji'] else "addstickers"
                pack_url = f"https://t.me/{pack_type_url}/{pack['short_name']}"
                all_time_inserts.append(('all_time', i, pack['pack_title'], pack_url, now))

            cursor.executemany(
                "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES (?, ?, ?, ?, ?)",
                all_time_inserts
            )
            
            conn.commit()

    except Exception as e:
        logger.error(f"FATAL: The calculate_and_store_popular_packs job crashed: {e}", exc_info=True)


def get_popular_packs(list_type: str) -> List[sqlite3.Row]:
    """Retrieves a pre-calculated list of popular packs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT pack_title, pack_url FROM popular_packs WHERE list_type = ? ORDER BY rank ASC",
            (list_type,)
        )
        return cursor.fetchall()