# fileName: database.py
# ✨ NEW FILE

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from config import OWNER_ID


DB_FILE = "bot_data.db"
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
                    pack_input TEXT NOT NULL,
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

            # --- History Tables ---
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

            # Table for banned users
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY,
                    banned_by_admin_id INTEGER NOT NULL,
                    ban_date TIMESTAMP NOT NULL,
                    reason TEXT
                )
            """)


            conn.commit()
            logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database error during initialization: {e}", exc_info=True)



# --- User Logging ---
def add_or_update_user(user_id: int, username: Optional[str], full_name: str):
    """Adds a new user or updates their details if they already exist."""
    safe_full_name = full_name[:50]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cursor.fetchone() is None:
            # New user
            cursor.execute(
                "INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
                (user_id, username, datetime.now())
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


# --- Conversion Logging ---
def log_conversion_request(user_id: int, pack_input: str, is_emoji: bool) -> int:
    """Logs the start of a conversion request and returns the log ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversion_log (user_id, pack_input, is_emoji, status, request_time) VALUES (?, ?, ?, ?, ?)",
            (user_id, pack_input, is_emoji, "processing", datetime.now())
        )
        conn.commit()
        return cursor.lastrowid

def update_conversion_log(log_id: int, status: str, completion_time: datetime, duration: float):
    """Updates a conversion log entry and the user's aggregate stats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # First, update the detailed log
        cursor.execute(
            "UPDATE conversion_log SET status = ?, completion_time = ?, duration_seconds = ? WHERE log_id = ?",
            (status, completion_time, duration, log_id)
        )
        
        # Update the aggregate stats table
        # Get the user_id for this conversion
        cursor.execute("SELECT user_id FROM conversion_log WHERE log_id = ?", (log_id,))
        result = cursor.fetchone()
        if result:
            user_id = result['user_id']
            # Increment the correct counter
            if status == "completed":
                cursor.execute("UPDATE user_stats SET succeeded_requests = succeeded_requests + 1 WHERE user_id = ?", (user_id,))
            else: # "failed"
                cursor.execute("UPDATE user_stats SET failed_requests = failed_requests + 1 WHERE user_id = ?", (user_id,))
            # Always increment total requests
            cursor.execute("UPDATE user_stats SET total_requests = total_requests + 1 WHERE user_id = ?", (user_id,))

        conn.commit()


# --- Role Checks (Owner, Admin, Premium) ---
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
            (user_id, datetime.now())
        )
        return cursor.fetchone() is not None


# --- Admin Management ---
def add_admin(user_id: int, username: str, promoted_by: int):
    """Promotes a user to admin."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO admins (user_id, username, promoted_by, promotion_date) VALUES (?, ?, ?, ?)",
            (user_id, username, promoted_by, datetime.now())
        )
        conn.execute(
            "INSERT INTO admin_history (admin_id, target_user_id, action, timestamp) VALUES (?, ?, ?, ?)",
            (promoted_by, user_id, 'promoted', datetime.now())
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
                (demoted_by, user_id, 'demoted', datetime.now())
            )
            conn.commit()
            return True
        return False

# --- Premium User Management ---
def add_premium(user_id: int, username: str, duration_days: int, added_by: int):
    """Adds or extends a user's premium subscription."""
    now = datetime.now()
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
                (admin_id, user_id, 'removed', None, previous_expiry, None, datetime.now())
            )
            conn.commit()
            return True
        return False

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
            (admin_id, user_id, action, days, current_expiry, new_expiry, datetime.now())
        )
        conn.commit()

        return new_expiry
    

# --- Ban Management ---

def is_banned(user_id: int) -> bool:
    """Checks if a user is in the banned table. This is the fast check."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def ban_user(user_id: int, admin_id: int, reason: Optional[str]):
    """Adds a user to the banned_users table."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, banned_by_admin_id, ban_date, reason) VALUES (?, ?, ?, ?)",
            (user_id, admin_id, datetime.now(), reason)
        )
        conn.commit()

def unban_user(user_id: int) -> bool:
    """Removes a user from the banned_users table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0
