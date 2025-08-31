
import psycopg2
import psycopg2.extras
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from config import (OWNER_ID, DATA_DIR, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT)


logger = logging.getLogger(__name__)

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.cursor_factory = psycopg2.extras.DictCursor
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"Could not connect to PostgreSQL database: {e}")
        raise

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # For logging all unique users who start the bot
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_seen TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)

            # Table for user stats
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    total_requests INTEGER DEFAULT 0,
                    succeeded_requests INTEGER DEFAULT 0,
                    failed_requests INTEGER DEFAULT 0,
                    cancelled_requests INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            """)

            # For logging every single conversion request
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversion_log (
                    log_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    set_id BIGINT NOT NULL, 
                    pack_url TEXT NOT NULL,
                    is_emoji BOOLEAN NOT NULL,
                    status TEXT NOT NULL,
                    request_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    completion_time TIMESTAMP WITH TIME ZONE,
                    duration_seconds REAL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # For storing current admins
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    promoted_by BIGINT NOT NULL,
                    promotion_date TIMESTAMP WITH TIME ZONE NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            """)

            # For storing premium users and their expiration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS premium_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    added_by BIGINT NOT NULL,
                    start_date TIMESTAMP WITH TIME ZONE NOT NULL,
                    expiry_date TIMESTAMP WITH TIME ZONE NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                )
            """)

            # admin_history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_history (
                    history_id SERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    target_user_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    action_time TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)
            
            # premium_history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS premium_history (
                    history_id SERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    target_user_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    duration_change_days INTEGER,
                    previous_expiry_date TIMESTAMP WITH TIME ZONE,
                    new_expiry_date TIMESTAMP WITH TIME ZONE,
                    action_time TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)

            # Table for currently banned users
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY,
                    banned_by_admin_id BIGINT NOT NULL,
                    ban_date TIMESTAMP WITH TIME ZONE NOT NULL,
                    is_silent BOOLEAN NOT NULL,
                    reason TEXT
                )
            """)

            # Table for logging all ban/unban actions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ban_history (
                    history_id SERIAL PRIMARY KEY,
                    target_user_id BIGINT NOT NULL,
                    admin_id BIGINT NOT NULL,
                    action TEXT NOT NULL, -- 'banned' or 'unbanned'
                    is_silent_ban BOOLEAN, -- TRUE for /sban, FALSE for /ban, NULL for unban
                    reason TEXT,
                    action_time TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)

            # For the user's initial contact message
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contact_messages (
                    contact_id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    user_message_id BIGINT NOT NULL,
                    user_message_text TEXT,
                    action_time_sent TIMESTAMP WITH TIME ZONE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' or 'replied'
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # For logging all admin replies
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_replies (
                    reply_id SERIAL PRIMARY KEY,
                    contact_id INTEGER NOT NULL,
                    admin_id BIGINT NOT NULL,
                    admin_reply_message_id BIGINT NOT NULL,
                    admin_reply_text TEXT,
                    action_time_replied TIMESTAMP WITH TIME ZONE NOT NULL,
                    FOREIGN KEY (contact_id) REFERENCES contact_messages (contact_id),
                    FOREIGN KEY (admin_id) REFERENCES users (user_id)
                )
            """)

            # For logging all broadcast messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_log (
                    broadcast_id SERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    action_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    message_content TEXT,
                    flags TEXT,
                    total_users INTEGER,
                    success_count INTEGER,
                    fail_count INTEGER,
                    is_forward BOOLEAN DEFAULT FALSE,
                    forwarded_from_id BIGINT,
                    forwarded_message_id BIGINT,
                    FOREIGN KEY (admin_id) REFERENCES users (user_id)
                )
            """)

            # For logging all /send messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS send_log (
                    send_id SERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    action_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    message_content TEXT,
                    flags TEXT,
                    target_users_list JSONB,
                    total_users INTEGER,
                    success_count INTEGER,
                    fail_count INTEGER,
                    is_forward BOOLEAN DEFAULT FALSE,
                    forwarded_from_id BIGINT,
                    forwarded_message_id BIGINT,
                    FOREIGN KEY (admin_id) REFERENCES users (user_id)
                )
            """)
            
            # sticker set stats 
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sticker_set_stats (
                    set_id BIGINT PRIMARY KEY,
                    short_name TEXT UNIQUE NOT NULL,
                    is_emoji BOOLEAN NOT NULL,
                    pack_title TEXT,
                    sticker_count INTEGER,
                    request_count INTEGER DEFAULT 1,
                    last_conversion_duration REAL,
                    cache_score REAL DEFAULT 0.0,
                    last_updated TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)

            # For tracking which packs are currently cached
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cached_packs (
                    set_id BIGINT PRIMARY KEY,
                    cache_score REAL NOT NULL,
                    cached_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    channel_id BIGINT NOT NULL,
                    message_ids JSONB NOT NULL,
                    FOREIGN KEY (set_id) REFERENCES sticker_set_stats (set_id) ON DELETE CASCADE
                )
            """)

            # For tracking file counts in our cache channels
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache_channels (
                    channel_id BIGINT PRIMARY KEY,
                    file_count INTEGER NOT NULL DEFAULT 0
                )
            """)

            # For storing pre-calculated popular packs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS popular_packs (
                    list_type TEXT NOT NULL, -- 'daily' or 'all_time'
                    rank INTEGER NOT NULL,
                    pack_title TEXT NOT NULL,
                    pack_url TEXT NOT NULL,
                    last_updated TIMESTAMP WITH TIME ZONE NOT NULL,
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
            # Speeds up the daily popular packs calculation
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversion_log_set_id ON conversion_log (set_id)
            """)
            # Speeds up fetching top users and all-time popular packs
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sticker_set_stats_request_count ON sticker_set_stats (request_count)
            """)
            
            conn.commit()
            logger.info("Database initialized successfully.")
    except (psycopg2.Error, Exception) as e:
        logger.error(f"Database error during initialization: {e}", exc_info=True)
        raise


# User Logging
def add_or_update_user(user_id: int, username: Optional[str], full_name: str):
    """Adds a new user or updates their details if they already exist."""
    safe_full_name = full_name[:129]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # user update
        cursor.execute("""
            INSERT INTO users (user_id, username, first_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username;
        """, (user_id, username, now))
        # stats update
        cursor.execute("""
            INSERT INTO user_stats (user_id, username, full_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name;
        """, (user_id, username, safe_full_name))

        conn.commit()

def get_user_stats(user_id: int) -> dict:
    """Gets conversion stats for a specific user from the optimized table."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT total_requests, succeeded_requests, failed_requests, cancelled_requests FROM user_stats WHERE user_id = %s", (user_id,))
        stats = cursor.fetchone()
        if stats:
            return {"total": stats['total_requests'], "succeeded": stats['succeeded_requests'], "failed": stats['failed_requests'], "cancelled": stats['cancelled_requests']}
    return {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0}

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
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        query = """
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM admins) AS total_admins,
            (SELECT COUNT(*) FROM banned_users) AS total_banned,
            (SELECT COUNT(*) FROM premium_users WHERE expiry_date > %s) AS active_premium,
            (SELECT SUM(succeeded_requests) FROM user_stats) AS total_succeeded,
            (SELECT SUM(failed_requests) FROM user_stats) AS total_failed,
            (SELECT SUM(cancelled_requests) FROM user_stats) AS total_cancelled,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= %s AND status LIKE 'completed%%') AS today_succeeded,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= %s AND status LIKE 'failed%%') AS today_failed,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= %s AND status LIKE 'cancelled%%') AS today_cancelled;
        """

        cursor.execute(query, (datetime.now(timezone.utc), today_start, today_start, today_start))
        stats = cursor.fetchone()
        
        return {
            "total_users": stats['total_users'],
            "total_admins": stats['total_admins'],
            "total_banned": stats['total_banned'],
            "active_premium": stats['active_premium'],
            "total_succeeded": stats['total_succeeded'] or 0,
            "total_failed": stats['total_failed'] or 0,
            "total_cancelled": stats['total_cancelled'] or 0,
            "today_succeeded": stats['today_succeeded'],
            "today_failed": stats['today_failed'],
            "today_cancelled": stats['today_cancelled']
        }

def get_gstats_premium_list() -> list:
    """Gets a list of all active premium users for /gstats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username, expiry_date FROM premium_users WHERE expiry_date > %s ORDER BY expiry_date ASC",
            (datetime.now(timezone.utc).replace(microsecond=0),)
        )
        return cursor.fetchall()

def get_gstats_top_users(limit: int = 50) -> list:
    """Gets a list of top users by total requests."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, full_name, total_requests FROM user_stats ORDER BY total_requests DESC LIMIT %s",
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
def log_conversion_request(user_id: int, set_id: int, pack_url: str, is_emoji: bool) -> int:
    """Logs the start of a conversion request and returns the log ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO conversion_log (user_id, set_id, pack_url, is_emoji, status, request_time) 
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING log_id
            """,
            (user_id, set_id, pack_url, is_emoji, "processing", datetime.now(timezone.utc).replace(microsecond=0))
        )
        log_id = cursor.fetchone()['log_id']
        conn.commit()
        return log_id

def update_conversion_log(log_id: int, status: str, completion_time: datetime, duration: float):
    """Updates a conversion log entry and the user's stats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # First update the detailed log
        cursor.execute(
            "UPDATE conversion_log SET status = %s, completion_time = %s, duration_seconds = %s WHERE log_id = %s RETURNING user_id",
            (status, completion_time.replace(microsecond=0), round(duration, 2), log_id)
        )
        row = cursor.fetchone()
        if not row:
            return  # No such log_id
        user_id = row[0]

        # Update the stats table
        # Increment the correct counter
        if status.startswith("completed"):
            cursor.execute("UPDATE user_stats SET succeeded_requests = succeeded_requests + 1, total_requests = total_requests + 1 WHERE user_id = %s", (user_id,))
        elif status.startswith("failed"):
            cursor.execute("UPDATE user_stats SET failed_requests = failed_requests + 1, total_requests = total_requests + 1 WHERE user_id = %s", (user_id,))
        elif status.startswith("cancelled"):
            cursor.execute("UPDATE user_stats SET cancelled_requests = cancelled_requests + 1, total_requests = total_requests + 1 WHERE user_id = %s", (user_id,))
        else:
            cursor.execute("UPDATE user_stats SET total_requests = total_requests + 1 WHERE user_id = %s", (user_id,))
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
        cursor.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
        return cursor.fetchone() is not None

def is_premium(user_id: int) -> bool:
    """Checks if a user has an active premium subscription."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT expiry_date FROM premium_users WHERE user_id = %s AND expiry_date > %s",
            (user_id, datetime.now(timezone.utc).replace(microsecond=0))
        )
        return cursor.fetchone() is not None

#################  Admin Management ###########
def add_admin(user_id: int, username: str, promoted_by: int):
    """Promotes a user to admin."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO admins (user_id, username, promoted_by, promotion_date) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                promoted_by = EXCLUDED.promoted_by,
                promotion_date = EXCLUDED.promotion_date
            """,
            (user_id, username, promoted_by, datetime.now(timezone.utc).replace(microsecond=0))
        )
        cursor.execute(
            "INSERT INTO admin_history (admin_id, target_user_id, action, action_time) VALUES (%s, %s, %s, %s)",
            (promoted_by, user_id, 'promoted', datetime.now(timezone.utc).replace(microsecond=0))
        )
        conn.commit()

def remove_admin(user_id: int, demoted_by: int) -> bool:
    """Demotes an admin."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = %s", (user_id,))
        if cursor.rowcount > 0:
            cursor.execute(
                "INSERT INTO admin_history (admin_id, target_user_id, action, action_time) VALUES (%s, %s, %s, %s)",
                (demoted_by, user_id, 'demoted', datetime.now(timezone.utc).replace(microsecond=0))
            )
            conn.commit()
            return True
        return False

############# Premium User Management ############

def add_premium(user_id: int, username: str, duration_days: int, added_by: int):
    """Adds or extends a user's premium subscription."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expiry_date = now + timedelta(days=duration_days)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO premium_users (user_id, username, added_by, start_date, expiry_date) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                added_by = EXCLUDED.added_by,
                start_date = EXCLUDED.start_date,
                expiry_date = EXCLUDED.expiry_date
            """,
            (user_id, username, added_by, now, expiry_date)
        )
        # Log to history with all details
        cursor.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (added_by, user_id, 'added', duration_days, None, expiry_date, now)
        )
        conn.commit()

def remove_premium(user_id: int, admin_id: int) -> bool:
    """Removes a user's premium subscription."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Get the current expiry date BEFORE deleting
        cursor.execute("SELECT expiry_date FROM premium_users WHERE user_id = %s", (user_id,))
        current_premium = cursor.fetchone()
        previous_expiry = current_premium['expiry_date'] if current_premium else None

        cursor.execute("DELETE FROM premium_users WHERE user_id = %s", (user_id,))
        if cursor.rowcount > 0:
            # Log the removal action
            cursor.execute(
                "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (admin_id, user_id, 'removed', None, previous_expiry, None, datetime.now(timezone.utc).replace(microsecond=0))
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
            "SELECT expiry_date FROM premium_users WHERE user_id = %s AND expiry_date > %s",
            (user_id, datetime.now(timezone.utc).replace(microsecond=0))
        )
        result = cursor.fetchone()
        if result:
            expiry_date = result['expiry_date']
            return expiry_date - datetime.now(timezone.utc).replace(microsecond=0)
    return None

def manage_premium_duration(user_id: int, days: int, admin_id: int, action: str) -> Optional[datetime]:
    """Extends or deducts days from a premium subscription. 'days' can be negative for deduction."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT expiry_date FROM premium_users WHERE user_id = %s", (user_id,))
        current_premium = cursor.fetchone()

        if not current_premium:
            return None # User is not premium, cannot extend/deduct

        current_expiry = current_premium['expiry_date']
        new_expiry = current_expiry + timedelta(days=days)

        # Update the expiry date
        cursor.execute("UPDATE premium_users SET expiry_date = %s WHERE user_id = %s", (new_expiry, user_id))

        # Log this action to history
        cursor.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (admin_id, user_id, action, days, current_expiry, new_expiry, datetime.now(timezone.utc).replace(microsecond=0))
        )
        conn.commit()

        return new_expiry

def remove_expired_premium_users() -> int:
    """
    Finds and removes premium users whose subscriptions have expired.
    Logs the removal to the premium_history table as an 'expired' action.
    Returns the number of users removed.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    system_admin_id = 0 # Using 0 as a special ID for automated system actions

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # First, find all users whose subscriptions have expired to log them
        cursor.execute(
            "SELECT user_id, expiry_date FROM premium_users WHERE expiry_date <= %s",
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
            (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            history_logs
        )

        # Now delete the expired users from the main premium table
        cursor.execute("DELETE FROM premium_users WHERE expiry_date <= %s", (now,))
        
        removed_count = cursor.rowcount
        conn.commit()
        
        return removed_count

########## Ban Management ###########

def is_banned(user_id: int) -> bool:
    """Checks if a user is in the banned table. This is the fast check."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM banned_users WHERE user_id = %s", (user_id,))
        return cursor.fetchone() is not None

def ban_user(user_id: int, admin_id: int, reason: Optional[str], is_silent: bool):
    """Adds a user to the banned_users table and logs it to history."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Add to currently banned table
        cursor.execute(
            """
            INSERT INTO banned_users (user_id, banned_by_admin_id, ban_date, is_silent, reason) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                banned_by_admin_id = EXCLUDED.banned_by_admin_id,
                ban_date = EXCLUDED.ban_date,
                is_silent = EXCLUDED.is_silent,
                reason = EXCLUDED.reason
            """,
            (user_id, admin_id, now, is_silent, reason)
        )
        # Add to history log
        cursor.execute(
            "INSERT INTO ban_history (target_user_id, admin_id, action, is_silent_ban, reason, action_time) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, admin_id, 'banned', is_silent, reason, now)
        )
        conn.commit()

def unban_user(user_id: int, admin_id: int, reason: Optional[str]) -> bool:
    """Removes a user from the banned_users table and logs it to history."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM banned_users WHERE user_id = %s", (user_id,))
        
        # Only log if a user was actually removed
        if cursor.rowcount > 0:
            cursor.execute(
                "INSERT INTO ban_history (target_user_id, admin_id, action, reason, action_time) VALUES (%s, %s, %s, %s, %s)",
                (user_id, admin_id, 'unbanned', reason, datetime.now(timezone.utc).replace(microsecond=0))
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
            """
            INSERT INTO contact_messages (user_id, user_message_id, user_message_text, action_time_sent) 
            VALUES (%s, %s, %s, %s)
            RETURNING contact_id
            """,
            (user_id, user_message_id, message_text, datetime.now(timezone.utc).replace(microsecond=0))
        )
        contact_id = cursor.fetchone()['contact_id']
        conn.commit()
        logger.info(f"Logged new contact message from user {user_id}. contact_id: {contact_id}")
        return contact_id

def log_admin_reply(contact_id: int, admin_id: int, admin_reply_message_id: int, reply_text: str):
    """Logs an admin's reply and updates the original message status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Add the new reply to the replies table
        cursor.execute(
            "INSERT INTO admin_replies (contact_id, admin_id, admin_reply_message_id, admin_reply_text, action_time_replied) VALUES (%s, %s, %s, %s, %s)",
            (contact_id, admin_id, admin_reply_message_id, reply_text, datetime.now(timezone.utc).replace(microsecond=0))
        )
        # Mark the original message as 'replied'
        cursor.execute(
            "UPDATE contact_messages SET status = 'replied' WHERE contact_id = %s",
            (contact_id,)
        )
        conn.commit()
        logger.info(f"Logged admin reply for contact_id {contact_id} by admin {admin_id}")

def get_previous_replies(contact_id: int) -> List[psycopg2.extras.DictRow]:
    """Checks for and returns any previous replies for a given contact_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admin_replies WHERE contact_id = %s ORDER BY action_time_replied ASC", (contact_id,))
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
            WHERE cm.contact_id = %s
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
            WHERE ar.contact_id = %s
            ORDER BY ar.action_time_replied ASC
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
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO broadcast_log (
                admin_id, action_time, message_content, flags, total_users,
                success_count, fail_count, is_forward, forwarded_from_id,
                forwarded_message_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (admin_id, datetime.now(timezone.utc).replace(microsecond=0), message_content, flags, total_users,
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
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO send_log (
                admin_id, action_time, message_content, flags, target_users_list,
                total_users, success_count, fail_count, is_forward, forwarded_from_id,
                forwarded_message_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (admin_id, datetime.now(timezone.utc).replace(microsecond=0), message_content, flags, json.dumps(target_users_list),
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
                        
        cursor.execute(f"""
            INSERT INTO sticker_set_stats (
                set_id, short_name, is_emoji, pack_title, sticker_count, 
                request_count, last_conversion_duration, cache_score, last_updated
            )
            VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s)
            ON CONFLICT(set_id) DO UPDATE SET
                pack_title = EXCLUDED.pack_title,
                short_name = EXCLUDED.short_name,
                sticker_count = EXCLUDED.sticker_count,
                request_count = CASE 
                                  WHEN %s THEN sticker_set_stats.request_count 
                                  ELSE sticker_set_stats.request_count + 1 
                                END,
                last_conversion_duration = EXCLUDED.last_conversion_duration,
                cache_score = ({CACHE_SCORE_TIME_WEIGHT} * EXCLUDED.last_conversion_duration) + 
                              ({CACHE_SCORE_REQUEST_WEIGHT} * (
                                  CASE 
                                    WHEN %s THEN sticker_set_stats.request_count 
                                    ELSE sticker_set_stats.request_count + 1 
                                  END
                              )),
                last_updated = EXCLUDED.last_updated
            RETURNING cache_score;
        """, (
            set_id, short_name, is_emoji, pack_title, sticker_count, 
            round(conversion_duration, 2), 
            # This is the initial cache score for a NEW pack (request_count = 1)
            (CACHE_SCORE_TIME_WEIGHT * conversion_duration) + (CACHE_SCORE_REQUEST_WEIGHT * 1),
            datetime.now(timezone.utc).replace(microsecond=0),
            # Parameters for the ON CONFLICT part
            is_system_process,
            is_system_process
        ))
        
        new_cache_score = cursor.fetchone()['cache_score']
        
        conn.commit()
        
        logger.info(f"Updated stats for pack {short_name} (ID: {set_id}). New score: {new_cache_score:.2f}")
        return new_cache_score

def get_or_create_cache_channel() -> Optional[int]:
    """Finds a cache channel with space, or returns the next available one."""
    from config import CACHE_CHANNEL_IDS, MAX_FILES_PER_CACHE_CHANNEL
    if not CACHE_CHANNEL_IDS:
        return None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for channel_id in CACHE_CHANNEL_IDS:
            cursor.execute("SELECT file_count FROM cache_channels WHERE channel_id = %s", (channel_id,))
            result = cursor.fetchone()
            if result:
                if result['file_count'] < MAX_FILES_PER_CACHE_CHANNEL:
                    return channel_id
            else:
                # This channel is not in our DB yet, let's add it and use it.
                cursor.execute("INSERT INTO cache_channels (channel_id, file_count) VALUES (%s, %s)", (channel_id, 0))
                conn.commit()
                return channel_id
    # If we get here, all configured channels are full
    logger.error("All available cache channels are full!")
    return None

def update_cache_channel_file_count(channel_id: int, file_delta: int):
    """Increments or decrements the file count for a cache channel."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cache_channels SET file_count = file_count + %s WHERE channel_id = %s",
            (file_delta, channel_id)
        )
        conn.commit()

def is_pack_cached(set_id: int, current_title: str, current_sticker_count: int, is_system_process: Optional[int] = False) -> Tuple[Optional[str], Optional[int], Optional[List[int]]]:
    """
    Checks if a pack is cached and if the cache is up-to-date.
    Returns: A tuple (status, channel_id, message_ids)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = %s", (set_id,))
        cache_result = cursor.fetchone()

        if not cache_result:
            return 'miss', None, None

        channel_id = cache_result['channel_id']
        message_ids = cache_result['message_ids']

        cursor.execute("SELECT pack_title, sticker_count FROM sticker_set_stats WHERE set_id = %s", (set_id,))
        stats_result = cursor.fetchone()

        if not stats_result or \
           stats_result['pack_title'] != current_title or \
           stats_result['sticker_count'] != current_sticker_count:
            logger.warning(f"Stale cache detected for pack {set_id}.")
            return 'stale', channel_id, message_ids

        # It's in the cache and the stats match. It's a valid hit!
        # As a bonus, we'll update its stats to reflect this new request, keeping it relevant.
        cursor.execute("SELECT short_name, is_emoji, last_conversion_duration FROM sticker_set_stats WHERE set_id = %s", (set_id,))
        pack_info = cursor.fetchone()
        if pack_info:
            add_or_update_sticker_set_stats(
                set_id=set_id,
                short_name=pack_info['short_name'],
                is_emoji=pack_info['is_emoji'],
                pack_title=current_title,
                sticker_count=current_sticker_count,
                conversion_duration=pack_info['last_conversion_duration'],
                is_system_process=is_system_process
            )
        logger.info(f"Cache hit for pack {set_id} in channel {channel_id}.")
        return 'hit', channel_id, message_ids


def add_to_cache(set_id: int, cache_score: float, channel_id: int, message_ids: List[int]):
    """Adds a pack to the cache tracking table."""
    message_ids_json = json.dumps(message_ids)
    new_len = len(message_ids)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # See if this pack already exists
        cursor.execute("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = %s FOR UPDATE", (set_id,))
        old = cursor.fetchone()

        old_channel_id = old['channel_id'] if old else None
        old_len = len(old['message_ids']) if old else 0


        cursor.execute("""
            INSERT INTO cached_packs (set_id, cache_score, cached_at, channel_id, message_ids)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(set_id) DO UPDATE SET
                cache_score = EXCLUDED.cache_score,
                cached_at   = EXCLUDED.cached_at,
                channel_id  = EXCLUDED.channel_id,
                message_ids = EXCLUDED.message_ids
        """, (set_id, round(cache_score, 2), now, channel_id, message_ids_json))


        # Update the file count for the channel
        if old is None:
            # brand new pack
            cursor.execute(
                "UPDATE cache_channels SET file_count = file_count + %s WHERE channel_id = %s",
                (new_len, channel_id)
            )
        else:
            if old_channel_id == channel_id:
                # same channel, just adjust difference
                delta = new_len - old_len
                if delta:
                    cursor.execute(
                        "UPDATE cache_channels SET file_count = file_count + %s WHERE channel_id = %s",
                        (delta, channel_id)
                    )
            else:
                # moved pack from one channel to another
                cursor.execute(
                    "UPDATE cache_channels SET file_count = file_count - %s WHERE channel_id = %s",
                    (old_len, old_channel_id)
                )
                cursor.execute(
                    "UPDATE cache_channels SET file_count = file_count + %s WHERE channel_id = %s",
                    (new_len, channel_id)
                )
        conn.commit()
        logger.info(f"Added pack {set_id} to cache in channel {channel_id} with score {cache_score:.2f}")

def remove_from_cache(set_id: int) -> Optional[Tuple[int, List[int]]]:
    """Removes a pack from the cache table and returns its location for message deletion."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = %s", (set_id,))
        result = cursor.fetchone()
        if not result:
            return None
        
        channel_id = result['channel_id']
        message_ids = result['message_ids']

        cursor.execute("DELETE FROM cached_packs WHERE set_id = %s", (set_id,))
        # Update the file count for the channel (decrement)
        cursor.execute(
            "UPDATE cache_channels SET file_count = file_count - %s WHERE channel_id = %s",
            (len(message_ids), channel_id)
        )
        conn.commit()
        logger.info(f"Removed pack {set_id} from cache DB.")
        return channel_id, message_ids

def get_cached_pack_by_id(set_id: int) -> Optional[psycopg2.extras.DictRow]:
    """Gets the channel_id and message_ids for a single cached pack by its set_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = %s", (set_id,))
        return cursor.fetchone()

def get_all_cached_packs() -> List[int]:
    """Gets a list of all currently cached packs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT set_id FROM cached_packs")
        results =  cursor.fetchall()
        return [result['set_id'] for result in results]

def get_all_packs() -> List[str]:
    """Gets a list of all currently cached packs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT short_name FROM sticker_set_stats")
        results =  cursor.fetchall()
        return [result['short_name'] for result in results]

def get_set_id_by_short_name(short_name: str) -> Optional[int]:
    """Finds a sticker set's ID by its short name in sticker_set_stats."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT set_id FROM sticker_set_stats WHERE short_name = %s", (short_name,))
        result = cursor.fetchone()
        return result['set_id'] if result else None
    
def get_top_packs_by_score(limit: int) -> List[str]:
    """Gets the top N sticker packs ordered by their cache score."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT short_name FROM sticker_set_stats ORDER BY cache_score DESC LIMIT %s",
            (limit,)
        )
        #returns a list of shortname strings
        return [row['short_name'] for row in cursor.fetchall()]


def get_non_cached_packs(limit: Optional[int] = None) -> List[str]:
    """
    Gets a list of pack short_names from sticker_set_stats that are not in the cached_packs table.
    Results are ordered by cache_score descending to prioritize popular packs.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # find the entries in the sticker_set_stats that are not in cached_packs (on the basis of set_id)
        query = """
            SELECT sss.short_name
            FROM sticker_set_stats sss
            LEFT JOIN cached_packs cp ON sss.set_id = cp.set_id
            WHERE cp.set_id IS NULL
            ORDER BY sss.cache_score DESC
        """
        
        params = []
        if limit and isinstance(limit, int) and limit > 0:
            query += " LIMIT %s"
            params.append(limit)
            
        cursor.execute(query, params)
        
        # Returns a simple list of short_name strings
        return [row['short_name'] for row in cursor.fetchall()]

def get_cache_info() -> Tuple[int, Optional[psycopg2.extras.DictRow]]:
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
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_start = today_start - timedelta(days=1)
            
            # This query joins conversion logs with pack stats to get titles
            daily_query = """
                SELECT
                    sss.pack_title,
                    cl.pack_url,
                    COUNT(cl.set_id) as request_count
                FROM conversion_log cl
                JOIN sticker_set_stats sss ON cl.set_id = sss.set_id
                WHERE cl.request_time >= %s AND cl.request_time < %s
                GROUP BY sss.pack_title, cl.pack_url, cl.set_id
                ORDER BY request_count DESC
                LIMIT 10;
            """
            cursor.execute(daily_query, (yesterday_start, today_start))
            daily_packs = cursor.fetchall()

            daily_inserts = []
            for i, pack in enumerate(daily_packs, 1):
                daily_inserts.append(('daily', i, pack['pack_title'], pack['pack_url'], now))
            
            cursor.executemany(
                "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES (%s, %s, %s, %s, %s)",
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
                "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES (%s, %s, %s, %s, %s)",
                all_time_inserts
            )
            
            conn.commit()

    except Exception as e:
        logger.error(f"FATAL: The calculate_and_store_popular_packs job crashed: {e}", exc_info=True)


def get_popular_packs(list_type: str) -> List[psycopg2.extras.DictRow]:
    """Retrieves a pre-calculated list of popular packs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT pack_title, pack_url FROM popular_packs WHERE list_type = %s ORDER BY rank ASC",
            (list_type,)
        )
        return cursor.fetchall()