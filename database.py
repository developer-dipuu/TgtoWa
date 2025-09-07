
import asyncpg
import logging
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from config import (OWNER_ID, DATA_DIR, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT)

_pool: Optional[asyncpg.Pool] = None

logger = logging.getLogger(__name__)

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

async def init_pool():
    """Call this once at startup (e.g., before starting Telethon)."""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT,
            min_size=5,
            max_size=20,
            command_timeout=10,
            statement_cache_size=1000,
            max_inactive_connection_lifetime=300
        )
        return _pool
    except Exception as e:
        raise RuntimeError(f"Could not create DB pool! Exception: {e}")

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool:
        return _pool
    raise RuntimeError("Could not get DB pool!")

async def init_db():
    """Initializes the database and creates tables if they don't exist."""
            
    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
            
                # For logging all unique users who start the bot
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        first_seen TIMESTAMP WITH TIME ZONE NOT NULL
                    )
                """)

                # Table for user stats
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_stats (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        full_name TEXT,
                        total_requests INTEGER DEFAULT 0,
                        succeeded_requests INTEGER DEFAULT 0,
                        failed_requests INTEGER DEFAULT 0,
                        cancelled_requests INTEGER DEFAULT 0,
                        daily_requests INTEGER DEFAULT 0,
                        last_request_date DATE,
                        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                    )
                """)

                # For logging every single conversion request
                await conn.execute("""
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
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS admins (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        promoted_by BIGINT NOT NULL,
                        promotion_date TIMESTAMP WITH TIME ZONE NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                    )
                """)

                # For storing premium users and their expiration
                await conn.execute("""
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
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS admin_history (
                        history_id SERIAL PRIMARY KEY,
                        admin_id BIGINT NOT NULL,
                        target_user_id BIGINT NOT NULL,
                        action TEXT NOT NULL,
                        action_time TIMESTAMP WITH TIME ZONE NOT NULL
                    )
                """)
                
                # premium_history table
                await conn.execute("""
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
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS banned_users (
                        user_id BIGINT PRIMARY KEY,
                        banned_by_admin_id BIGINT NOT NULL,
                        ban_date TIMESTAMP WITH TIME ZONE NOT NULL,
                        is_silent BOOLEAN NOT NULL,
                        reason TEXT
                    )
                """)

                # Table for logging all ban/unban actions
                await conn.execute("""
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
                await conn.execute("""
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
                await conn.execute("""
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
                await conn.execute("""
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
                await conn.execute("""
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
                await conn.execute("""
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
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS cached_packs (
                        set_id BIGINT PRIMARY KEY,
                        cache_score REAL NOT NULL,
                        cached_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        channel_id BIGINT NOT NULL,
                        message_ids JSONB NOT NULL,
                        FOREIGN KEY (set_id) REFERENCES sticker_set_stats (set_id) ON DELETE CASCADE
                    )
                """)

                # For tracking messages that failed to be deleted from cache (e.g. > 48h old)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS junk_files (
                        junk_id SERIAL PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL,
                        set_id BIGINT,
                        reason TEXT,
                        logged_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        UNIQUE (channel_id, message_id)
                    )
                """)

                # For tracking file counts in our cache channels
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_channels (
                        channel_id BIGINT PRIMARY KEY,
                        file_count INTEGER NOT NULL DEFAULT 0
                    )
                """)

                # For storing pre-calculated popular packs
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS popular_packs (
                        list_type TEXT NOT NULL, -- 'daily' or 'all_time'
                        rank INTEGER NOT NULL,
                        pack_title TEXT NOT NULL,
                        pack_url TEXT NOT NULL,
                        last_updated TIMESTAMP WITH TIME ZONE NOT NULL,
                        PRIMARY KEY (list_type, rank)
                    )
                """)

                # For managing the conversion queue
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS queue (
                        id BIGSERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        chat_id BIGINT NOT NULL,
                        set_id BIGINT,
                        priority INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'waiting',
                        added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        processing_started_at TIMESTAMP WITH TIME ZONE,
                        log_id BIGINT UNIQUE,
                        item_data JSONB NOT NULL
                    )
                """)

                # For managing user interaction sessions
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        user_id BIGINT NOT NULL,
                        flow TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        state TEXT,
                        payload JSONB,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        expires_at TIMESTAMP WITH TIME ZONE,
                        PRIMARY KEY (user_id, flow, session_id)
                    )
                """)

                # ---- Indexes for fast lookups ----

                # For faster cache score lookups (for replacing cache and all)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_cached_packs_score ON cached_packs (cache_score)
                """)
                
                # Speeds up the daily premium user cleanup job and /gstats list
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_premium_users_expiry_date ON premium_users (expiry_date)
                """)
                # Speeds up /refreshcache by quickly sorting all packs
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sticker_set_stats_cache_score ON sticker_set_stats (cache_score)
                """)
                # Speeds up the daily stats calculation for /gstats (this is must as the conversion_log will get damn large)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversion_log_request_time ON conversion_log (request_time)
                """)
                # Speeds up fetching admin reply history
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_admin_replies_contact_id ON admin_replies (contact_id)
                """)
                # Speeds up fetching the top users list in /gstats (i do this damn often so bettr have this)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_user_stats_total_requests ON user_stats (total_requests)
                """)
                # Speeds up the daily popular packs calculation
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversion_log_set_id ON conversion_log (set_id)
                """)
                # Speeds up fetching top users and all-time popular packs
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sticker_set_stats_request_count ON sticker_set_stats (request_count)
                """)

                # Index for fetching the next available queue item fast
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_queue_waiting_priority ON queue (priority, added_at) WHERE status = 'waiting'
                """)
                
                # Index for quickly finding a user's items
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_queue_user_id ON queue (user_id, status)
                """)

                # Index for cleaning up expired sessions
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at)
                """)
                # To check if an item exists in the payload or not
                await conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_sessions_payload_gin ON sessions USING GIN (payload)
                """)
                # Index for finding a sticker set in the queue fast
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_queue_set_id ON queue (set_id) WHERE status IN ('waiting', 'processing')
                """) 
                # Index for grouping junk files by channel
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_junk_files_channel_id ON junk_files (channel_id)
                """)

        logger.info("Database initialized successfully.")
    except (asyncpg.PostgresError, Exception) as e:
        logger.error(f"Database error during initialization: {e}", exc_info=True)
        raise


# User Logging
async def add_or_update_user(user_id: int, username: Optional[str], full_name: str):
    """Adds a new user or updates their details if they already exist."""
    safe_full_name = full_name[:129]
    now = utcnow()
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # user update
            await conn.execute("""
                INSERT INTO users (user_id, username, first_seen)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username;
            """, user_id, username, now)
            # stats update
            await conn.execute("""
                INSERT INTO user_stats (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name;
            """, user_id, username, safe_full_name)


async def get_user_stats(user_id: int) -> dict:
    """Gets conversion stats for a specific user from the optimized table."""
    row = await _pool.fetchrow("""
        SELECT total_requests, succeeded_requests, failed_requests, cancelled_requests
        FROM user_stats WHERE user_id = $1
    """, user_id)
    if row:
        return {
            "total": row["total_requests"] or 0,
            "succeeded": row["succeeded_requests"] or 0,
            "failed": row["failed_requests"] or 0,
            "cancelled": row["cancelled_requests"] or 0,
        }
    return {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0}

async def get_daily_usage(user_id: int) -> int:
    """
    Gets the number of requests a user has made today.
    Returns 0 if the last request was on a previous day.
    """
    row = await _pool.fetchrow(
        "SELECT daily_requests, last_request_date FROM user_stats WHERE user_id = $1", user_id
    )
    if row and row['last_request_date']:
        today = utcnow().date()
        if row['last_request_date'] == today:
            return row['daily_requests'] or 0
    return 0

async def increment_daily_requests(user_id: int):
    """
    Increments the daily request counter for a user.
    If the last request was not today, it resets the counter to 1.
    """
    today = utcnow().date()
    await _pool.execute("""
        UPDATE user_stats
        SET
            daily_requests = CASE
                WHEN last_request_date = $2 THEN daily_requests + 1
                ELSE 1
            END,
            last_request_date = $2
        WHERE user_id = $1
    """, user_id, today)

async def decrement_daily_requests(user_id: int):
    """
    Decrements the daily request counter for a user, e.g., after a cancellation.
    Only decrements if the last request was today and the count is positive.
    """
    today = utcnow().date()
    await _pool.execute("""
        UPDATE user_stats
        SET daily_requests = daily_requests - 1
        WHERE user_id = $1 AND last_request_date = $2 AND daily_requests > 0
    """, user_id, today)

# fetching all user ids from database
async def get_all_user_ids() -> list[int]:
    """Retrieves all user IDs from the database for broadcasting."""
    rows = await _pool.fetch("SELECT user_id FROM users")
    return [row["user_id"] for row in rows]
    
# fetch all admins including owner from the database
async def get_all_admin_ids() -> List[int]:
    """Retrieves all admin and owner IDs from the database."""
    rows = await _pool.fetch("SELECT user_id FROM admins")
    admin_ids = [row['user_id'] for row in rows]
    # Ensure owner is always included and the list is unique
    if OWNER_ID not in admin_ids:
        admin_ids.append(OWNER_ID)
    return admin_ids

# for the owner's /gstats command
async def get_gstats() -> dict:
    """Gathers all global statistics for the /gstats command."""
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    row = await _pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM admins) AS total_admins,
            (SELECT COUNT(*) FROM banned_users) AS total_banned,
            (SELECT COUNT(*) FROM premium_users WHERE expiry_date > NOW()) AS active_premium,
            (SELECT COALESCE(SUM(succeeded_requests), 0) FROM user_stats) AS total_succeeded,
            (SELECT COALESCE(SUM(failed_requests), 0) FROM user_stats) AS total_failed,
            (SELECT COALESCE(SUM(cancelled_requests), 0) FROM user_stats) AS total_cancelled,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'completed%%') AS today_succeeded,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'failed%%') AS today_failed,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'cancelled%%') AS today_cancelled

    """, today_start)
    return dict(row)

async def get_gstats_premium_list() -> list:
    """Gets a list of all active premium users for /gstats."""
    now = utcnow()
    list = await _pool.fetch(
        "SELECT user_id, username, expiry_date FROM premium_users WHERE expiry_date > $1 ORDER BY expiry_date ASC",
        now
    )
    return list

async def get_gstats_top_users(limit: int = 50) -> list:
    """Gets a list of top users by total requests."""
    list = await _pool.fetch(
        "SELECT user_id, full_name, total_requests FROM user_stats ORDER BY total_requests DESC LIMIT $1",
        limit
    )
    return list

async def get_gstats_admins_list() -> list:
    """Gets a list of all admins."""
    list = await _pool.fetch("SELECT user_id, username FROM admins")
    return list

async def get_gstats_banned_list() -> list:
    """Gets a list of all banned users."""
    list = await _pool.fetch("SELECT user_id, reason, ban_date FROM banned_users ORDER BY ban_date DESC")
    return list
    
    
# Conversion Logging
async def log_conversion_request(user_id: int, set_id: int, pack_url: str, is_emoji: bool) -> int:
    """Logs the start of a conversion request and returns the log ID."""
    row = await _pool.fetchrow("""
        INSERT INTO conversion_log (user_id, set_id, pack_url, is_emoji, status, request_time)
        VALUES ($1, $2, $3, $4, 'processing', $5)
        RETURNING log_id
    """, user_id, set_id, pack_url, is_emoji, utcnow())
    return row["log_id"]

async def update_conversion_log(log_id: int, status: str, completion_time: datetime, duration: float):
    """Updates a conversion log entry and the user's stats."""
    async with _pool.acquire() as conn, conn.transaction():
        # First update the detailed log
        row = await conn.fetchrow("UPDATE conversion_log SET status = $1, completion_time = $2, duration_seconds = $3 WHERE log_id = $4 RETURNING user_id", 
            status, completion_time.replace(microsecond=0), round(duration, 2), log_id
        )
        if not row:
            return
        user_id = row["user_id"]

        # Update the stats table, increment the correct counter
        if status.startswith("completed"):
            await conn.execute("UPDATE user_stats SET succeeded_requests = succeeded_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        elif status.startswith("failed"):
            await conn.execute("UPDATE user_stats SET failed_requests = failed_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        elif status.startswith("cancelled"):
            await conn.execute("UPDATE user_stats SET cancelled_requests = cancelled_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        else:
            await conn.execute("UPDATE user_stats SET total_requests = total_requests + 1 WHERE user_id = $1", user_id)


########### Role Checks (Owner, Admin, Premium) ###########
def is_owner(user_id: int) -> bool:
    """Checks if a user is the owner."""
    return user_id == OWNER_ID

async def is_admin(user_id: int) -> bool:
    """Checks if a user is an admin or the owner."""
    if is_owner(user_id):
        return True
    row = await _pool.fetchrow("SELECT 1 FROM admins WHERE user_id = $1", user_id)
    return row is not None

async def is_premium(user_id: int) -> bool:
    """Checks if a user has an active premium subscription."""
    row = await _pool.fetchrow(
        "SELECT 1 FROM premium_users WHERE user_id = $1 AND expiry_date > $2",
        user_id, utcnow()
    )
    return row is not None

#################  Admin Management ###########
async def add_admin(user_id: int, username: str, promoted_by: int):
    """Promotes a user to admin."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        await conn.execute("""
            INSERT INTO admins (user_id, username, promoted_by, promotion_date)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                promoted_by = EXCLUDED.promoted_by,
                promotion_date = EXCLUDED.promotion_date
        """, user_id, username, promoted_by, now
        )

        await conn.execute("""
            INSERT INTO admin_history (admin_id, target_user_id, action, action_time)
            VALUES ($1, $2, 'promoted', $3)
        """, promoted_by, user_id, now
        )

async def remove_admin(user_id: int, demoted_by: int) -> bool:
    """Demotes an admin."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        deleted = await conn.fetchval(
            "DELETE FROM admins WHERE user_id = $1 RETURNING 1", user_id
        )
        if deleted:
            await conn.execute("""
                INSERT INTO admin_history (admin_id, target_user_id, action, action_time)
                VALUES ($1, $2, 'demoted', $3)
            """, demoted_by, user_id, now
            )
            return True
    return False

############# Premium User Management ############

async def add_premium(user_id: int, username: str, duration_days: int, added_by: int):
    """Adds or extends a user's premium subscription."""
    now = utcnow()
    expiry_date = now + timedelta(days=duration_days)
    async with _pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO premium_users (user_id, username, added_by, start_date, expiry_date) 
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                added_by = EXCLUDED.added_by,
                start_date = EXCLUDED.start_date,
                expiry_date = EXCLUDED.expiry_date
            """,
            user_id, username, added_by, now, expiry_date
        )
        # Log to history with all details
        await conn.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) VALUES ($1, $2, 'added', $3, NULL, $4, $5)",
            added_by, user_id, duration_days, expiry_date, now
        )

async def remove_premium(user_id: int, admin_id: int) -> bool:
    """Removes a user's premium subscription."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        # Get the current expiry date BEFORE deleting
        prev_expiry = await conn.fetchval(
            "SELECT expiry_date FROM premium_users WHERE user_id = $1",
            user_id
        )

        deleted = await conn.fetchval(
            "DELETE FROM premium_users WHERE user_id = $1 RETURNING 1", user_id
        )
        if not deleted:
            return False
        
        # Log the removal action
        await conn.execute("""
            INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days,
                                         previous_expiry_date, new_expiry_date, action_time)
            VALUES ($1, $2, 'removed', NULL, $3, NULL, $4)
        """, admin_id, user_id, prev_expiry, now
        )
    return True
    
async def get_premium_duration_left(user_id: int) -> Optional[timedelta]:
    """
    Calculates the remaining duration for a premium user's subscription.
    Returns a timedelta object if the subscription is active, otherwise None.
    """
    now = utcnow()
    expiry_date = await _pool.fetchval(
        "SELECT expiry_date FROM premium_users WHERE user_id = $1 AND expiry_date > $2",
        user_id, now
    )
    if expiry_date:
        return expiry_date - now
    return None

async def manage_premium_duration(user_id: int, days: int, admin_id: int, action: str) -> datetime | None:
    """Extends or deducts days from a premium subscription. 'days' can be negative for deduction."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        # update expiry and return old and new expiry
        expiry = await conn.fetchrow("""
            UPDATE premium_users
            SET expiry_date = expiry_date + $1::interval
            WHERE user_id = $2
            AND expiry_date > $3
            RETURNING expiry_date - $1::interval AS old_expiry,
            expiry_date AS new_expiry
        """, f'{days} days', user_id, now
        )

        if not expiry:
            return None # User is not premium, cannot extend/deduct
        
        old_expiry = expiry['old_expiry']
        new_expiry = expiry['new_expiry']

        # Log this action to history
        await conn.execute(
            "INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days, previous_expiry_date, new_expiry_date, action_time) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            admin_id, user_id, action, days, old_expiry, new_expiry, now
        )

    return new_expiry

async def remove_expired_premium_users() -> int:
    """
    Finds and removes premium users whose subscriptions have expired.
    Logs the removal to the premium_history table as an 'expired' action.
    Returns the number of users removed.
    """
    now = utcnow()
    system_admin_id = 0 # Using 0 as a special ID for automated system actions

    async with _pool.acquire() as conn, conn.transaction():

        # First, find all users whose subscriptions have expired to log them
        expired_users = await conn.fetch(
            "SELECT user_id, expiry_date FROM premium_users WHERE expiry_date <= $1", now
        )

        if not expired_users:
            return 0 # No one to remove

        # Log that these users expired before we delete them
        history_logs = []
        for user in expired_users:
            # We log the action as 'expired' by the SYSTEM (admin_id=0)
            history_logs.append(
                (system_admin_id, user['user_id'], 'expired', None, user['expiry_date'], None, now)
            )
        
        await conn.executemany("""
            INSERT INTO premium_history (admin_id, target_user_id, action, duration_change_days,
                                         previous_expiry_date, new_expiry_date, action_time)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, history_logs)

        # Now delete the expired users from the main premium table
        deleted_rows = await conn.execute("DELETE FROM premium_users WHERE expiry_date <= $1 RETURNING 1" , now)
        removed_count = len(deleted_rows)    
    return removed_count

########## Ban Management ###########

async def is_banned(user_id: int) -> bool:
    """Checks if a user is in the banned table. This is the fast check."""
    return await _pool.fetchrow(
        "SELECT 1 FROM banned_users WHERE user_id = $1", user_id
    ) is not None

async def ban_user(user_id: int, admin_id: int, reason: Optional[str], is_silent: bool):
    """Adds a user to the banned_users table and logs it to history."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        # Add to currently banned table
        await conn.execute("""
            INSERT INTO banned_users (user_id, banned_by_admin_id, ban_date, is_silent, reason)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                banned_by_admin_id = EXCLUDED.banned_by_admin_id,
                ban_date = EXCLUDED.ban_date,
                is_silent = EXCLUDED.is_silent,
                reason = EXCLUDED.reason
        """, user_id, admin_id, now, is_silent, reason)

        # Add to history log
        await conn.execute("""
            INSERT INTO ban_history (target_user_id, admin_id, action, is_silent_ban, reason, action_time)
            VALUES ($1, $2, 'banned', $3, $4, $5)
        """, user_id, admin_id, is_silent, reason, now)

async def unban_user(user_id: int, admin_id: int, reason: Optional[str]) -> bool:
    """Removes a user from the banned_users table and logs it to history."""
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        deleted = await conn.fetchval(
            "DELETE FROM banned_users WHERE user_id = $1 RETURNING 1", user_id
        )
        if not deleted:
            return False
        await conn.execute("""
            INSERT INTO ban_history (target_user_id, admin_id, action, reason, action_time)
            VALUES ($1, $2, 'unbanned', $3, $4)
        """, user_id, admin_id, reason, now)
    return True

############ Contact Logging #################

async def log_contact_message(user_id: int, user_message_id: int, message_text: str) -> int:
    """Logs a new contact message from a user into the contact_messages table and returns contact_id"""
    row = await _pool.fetchrow("""
        INSERT INTO contact_messages (user_id, user_message_id, user_message_text, action_time_sent)
        VALUES ($1, $2, $3, $4)
        RETURNING contact_id
    """, user_id, user_message_id, message_text, utcnow())
    contact_id = row['contact_id']
    logger.info(f"Logged new contact message from user {user_id}. contact_id: {contact_id}")
    return contact_id

async def log_admin_reply(contact_id: int, admin_id: int, admin_reply_message_id: int, reply_text: str):
    """Logs an admin's reply and updates the original message status."""
    async with _pool.acquire() as conn, conn.transaction():
        # Add the new reply to the replies table
        await conn.execute("""
            INSERT INTO admin_replies (contact_id, admin_id, admin_reply_message_id, admin_reply_text, action_time_replied)
            VALUES ($1, $2, $3, $4, $5)
        """, contact_id, admin_id, admin_reply_message_id, reply_text, utcnow())
        # Mark the original message as 'replied'
        await conn.execute("UPDATE contact_messages SET status = 'replied' WHERE contact_id = $1", contact_id)
    logger.info(f"Logged admin reply for contact_id {contact_id} by admin {admin_id}")

async def get_previous_replies(contact_id: int) -> List[asyncpg.Record]:
    """Checks for and returns any previous replies for a given contact_id."""
    return await _pool.fetch("SELECT * FROM admin_replies WHERE contact_id = $1 ORDER BY action_time_replied ASC", contact_id)

async def get_contact_details(contact_id: int) -> Optional[dict]:
    """
    Fetches full details for a contact ticket, including the user's info,
    the original message, and all admin replies with admin info.
    """
    async with _pool.acquire() as conn, conn.transaction():
        
        # Get the original message and join with user_stats to get the user's name
        contact_message = await _pool.fetchrow("""
            SELECT cm.*, us.full_name as user_full_name
            FROM contact_messages cm
            LEFT JOIN user_stats us ON cm.user_id = us.user_id
            WHERE cm.contact_id = $1
        """, contact_id
        )

        if not contact_message:
            return None

        # Get all admin replies and join with user_stats to get admin names
        replies = await _pool.fetch("""
            SELECT ar.*, us.full_name as admin_full_name
            FROM admin_replies ar
            LEFT JOIN user_stats us ON ar.admin_id = us.user_id
            WHERE ar.contact_id = $1
            ORDER BY ar.action_time_replied ASC
        """, contact_id)

    return {"user_message": contact_message, "admin_replies": replies}

############ Broadcast/send Logging #####################

async def log_broadcast(admin_id: int, message_content: str, flags: str,
                  total_users: int, success_count: int, fail_count: int,
                  is_forward: bool = False, forwarded_from_id: Optional[int] = None,
                  forwarded_message_id: Optional[int] = None):
    """Logs a broadcast event to the database."""
    await _pool.execute("""
        INSERT INTO broadcast_log (
            admin_id, action_time, message_content, flags, total_users,
            success_count, fail_count, is_forward, forwarded_from_id, forwarded_message_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """, admin_id, utcnow(), message_content, flags, total_users,
       success_count, fail_count, is_forward, forwarded_from_id, forwarded_message_id)
    
    logger.info(f"Logged broadcast from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")


async def log_send(admin_id: int, message_content: str, flags: str,
             target_users_list: List[int], success_count: int, fail_count: int,
             is_forward: bool = False, forwarded_from_id: Optional[int] = None,
             forwarded_message_id: Optional[int] = None):
    """Logs a /send event to the database."""
    await _pool.execute("""
        INSERT INTO send_log (
            admin_id, action_time, message_content, flags, target_users_list,
            total_users, success_count, fail_count, is_forward, forwarded_from_id,
            forwarded_message_id
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11)
    """, admin_id, utcnow(), message_content, flags, json.dumps(target_users_list),
       len(target_users_list), success_count, fail_count, is_forward, forwarded_from_id,
       forwarded_message_id)

    logger.info(f"Logged /send from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")


############### Sticker Pack Stats & Caching #################

async def add_or_update_sticker_set_stats(set_id: int, short_name: str, is_emoji: bool, pack_title: str, sticker_count: int, conversion_duration: float, is_system_process: bool) -> float:
    """
    Adds or updates a sticker pack's stats after a conversion.
    Calculates and returns the new cache score.
    """
    from config import CACHE_SCORE_TIME_WEIGHT, CACHE_SCORE_REQUEST_WEIGHT

    rounded_duration = round(conversion_duration, 2)
    now = utcnow()
    row = await _pool.fetchrow(f"""
        INSERT INTO sticker_set_stats (
            set_id, short_name, is_emoji, pack_title, sticker_count,
            request_count, last_conversion_duration, cache_score, last_updated
        )
        VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8)
        ON CONFLICT (set_id) DO UPDATE SET
            pack_title = EXCLUDED.pack_title,
            short_name = EXCLUDED.short_name,
            sticker_count = EXCLUDED.sticker_count,
            request_count = CASE WHEN $9 THEN sticker_set_stats.request_count
                                 ELSE sticker_set_stats.request_count + 1 END,
            last_conversion_duration = EXCLUDED.last_conversion_duration,
            cache_score = ({CACHE_SCORE_TIME_WEIGHT} * EXCLUDED.last_conversion_duration) +
                          ({CACHE_SCORE_REQUEST_WEIGHT} * (
                              CASE WHEN $9 THEN sticker_set_stats.request_count
                                   ELSE sticker_set_stats.request_count + 1 END
                          )),
            last_updated = EXCLUDED.last_updated
        RETURNING cache_score
    """, set_id, short_name, is_emoji, pack_title, sticker_count,
         rounded_duration,
         (CACHE_SCORE_TIME_WEIGHT * rounded_duration) + (CACHE_SCORE_REQUEST_WEIGHT * 1),
         now,
         is_system_process)
    
    new_cache_score = float(row["cache_score"])
    logger.info(f"Updated stats for pack {short_name} (ID: {set_id}). New score: {new_cache_score:.2f}")
    return new_cache_score

async def get_or_create_cache_channel() -> Optional[int]:
    """Finds a cache channel with space, or returns the next available one."""
    from config import CACHE_CHANNEL_IDS, MAX_FILES_PER_CACHE_CHANNEL
    if not CACHE_CHANNEL_IDS:
        return None

    async with _pool.acquire() as conn, conn.transaction():
        for channel_id in CACHE_CHANNEL_IDS:
            # Ensure row exists
            await conn.execute("""
                INSERT INTO cache_channels (channel_id, file_count)
                VALUES ($1, 0)
                ON CONFLICT (channel_id) DO NOTHING
            """, channel_id)
            # Lock row and read count
            row = await conn.fetchrow(
                "SELECT file_count FROM cache_channels WHERE channel_id = $1 FOR UPDATE",
                channel_id
            )

            if row and row["file_count"] < MAX_FILES_PER_CACHE_CHANNEL:
                return channel_id
        
    # If we get here, all configured channels are full
    logger.warning("⚠️ All available cache channels are full! ⚠️")
    return None

async def update_cache_channel_file_count(channel_id: int, file_delta: int):
    """Increments or decrements the file count for a cache channel."""
    await _pool.execute(
            "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
            file_delta, channel_id
        )

async def is_pack_cached(set_id: int, current_title: str, current_sticker_count: int) -> Tuple[Optional[str], Optional[int], Optional[List[int]]]:
    """
    Checks if a pack is cached and if the cache is up-to-date.
    Returns: A tuple (status, channel_id, message_ids)
    """
    async with _pool.acquire() as conn, conn.transaction():

        cache_result = await conn.fetchrow("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1", set_id,)

        if not cache_result:
            return 'miss', None, None

        channel_id = cache_result['channel_id']
        message_ids = json.loads(cache_result['message_ids'])

        stats_result = await conn.fetchrow("SELECT pack_title, sticker_count FROM sticker_set_stats WHERE set_id = $1", set_id,)

        if not stats_result or \
           stats_result['pack_title'] != current_title or \
           stats_result['sticker_count'] != current_sticker_count:
            logger.warning(f"Stale cache detected for pack {set_id}.")
            return 'stale', channel_id, message_ids
        
    logger.info(f"Cache hit for pack {set_id} in channel {channel_id}.")
    return 'hit', channel_id, message_ids

async def record_cache_hit(set_id: int, is_system_process: bool = False):
    """Updates a pack's stats to reflect a cache hit, increasing its score."""
    pack_info = await _pool.fetchrow(
        "SELECT short_name, is_emoji, pack_title, sticker_count, last_conversion_duration FROM sticker_set_stats WHERE set_id = $1",
        set_id
    )
    if pack_info:
        await add_or_update_sticker_set_stats(
            set_id=set_id,
            short_name=pack_info['short_name'],
            is_emoji=pack_info['is_emoji'],
            pack_title=pack_info['pack_title'],
            sticker_count=pack_info['sticker_count'],
            conversion_duration=pack_info['last_conversion_duration'],
            is_system_process=is_system_process
        )

async def add_to_cache(set_id: int, cache_score: float, channel_id: int, message_ids: List[int]):
    """Adds a pack to the cache tracking table."""
    new_len = len(message_ids)
    now = utcnow()
    async with _pool.acquire() as conn, conn.transaction():
        # See if this pack already exists
        old = await conn.fetchrow(
            "SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1 FOR UPDATE",
            set_id
        )
        old_channel_id = old["channel_id"] if old else None
        old_len = len(json.loads(old["message_ids"])) if old else 0

        await conn.execute("""
            INSERT INTO cached_packs (set_id, cache_score, cached_at, channel_id, message_ids)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (set_id) DO UPDATE SET
                cache_score = EXCLUDED.cache_score,
                cached_at   = EXCLUDED.cached_at,
                channel_id  = EXCLUDED.channel_id,
                message_ids = EXCLUDED.message_ids
        """, set_id, round(cache_score, 2), now, channel_id, json.dumps(message_ids))


        # Update the file count for the channel
        if old is None:
            # brand new pack
            await conn.execute(
                "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                new_len, channel_id
            )
        else:
            if old_channel_id == channel_id:
                # same channel, just adjust difference
                delta = new_len - old_len
                if delta:
                    await conn.execute(
                        "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                        delta, channel_id
                    )
            else:
                # moved pack from one channel to another
                await conn.execute(
                    "UPDATE cache_channels SET file_count = file_count - $1 WHERE channel_id = $2",
                    old_len, old_channel_id
                )
                await conn.execute(
                    "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                    new_len, channel_id
                )
    logger.info(f"Added pack {set_id} to cache in channel {channel_id} with score {cache_score:.2f}")

async def remove_from_cache(set_id: int) -> Optional[Tuple[int, List[int]]]:
    """Removes a pack from the cache table and returns its location for message deletion."""
    async with _pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "DELETE FROM cached_packs WHERE set_id = $1 RETURNING channel_id, message_ids",
            set_id
        )
        if not row:
            return None
        
        # Update the file count for the channel (decrement)
        channel_id, message_ids = row["channel_id"], json.loads(row["message_ids"])

        await conn.execute(
            "UPDATE cache_channels SET file_count = file_count - $1 WHERE channel_id = $2",
            len(message_ids), channel_id
        )

    logger.info(f"Removed pack {set_id} from cache DB.")
    return channel_id, message_ids

# currently not in use
async def get_cached_pack_by_id(set_id: int) -> asyncpg.Record | None:
    """Gets the channel_id and message_ids for a single cached pack by its set_id. WARNING: make sure to json.loads() when acessing message_ids"""
    return await _pool.fetchrow("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1", set_id)

async def get_all_cached_pack_ids() -> List[int]:
    """Gets a list of set IDs of all currently cached packs."""
    results = await _pool.fetch("SELECT set_id FROM cached_packs")
    return [result['set_id'] for result in results]

async def get_all_known_pack_short_names() -> List[str]:
    """Gets a list of short names of all currently known packs."""
    results = await _pool.fetch("SELECT short_name FROM sticker_set_stats")
    return [result['short_name'] for result in results]

async def get_set_id_by_short_name(short_name: str) -> Optional[int]:
    """Finds a sticker set's ID by its short name in sticker_set_stats."""
    return await _pool.fetchval("SELECT set_id FROM sticker_set_stats WHERE short_name = $1", short_name)

async def get_top_packs_by_score(limit: int) -> List[str]:
    """Gets the top N sticker packs ordered by their cache score, returns a list of shortname strings"""
    rows = await _pool.fetch(
            "SELECT short_name FROM sticker_set_stats ORDER BY cache_score DESC LIMIT $1",
            limit)
    return [row['short_name'] for row in rows]


async def get_non_cached_packs(limit: Optional[int] = None) -> List[str]:
    """
    Gets a list of pack short_names from sticker_set_stats that are not in the cached_packs table.
    Results are ordered by cache_score descending to prioritize popular packs.
    """ 
    # find the entries in the sticker_set_stats that are not in cached_packs (on the basis of set_id)
    query = """
        SELECT sss.short_name
        FROM sticker_set_stats sss
        LEFT JOIN cached_packs cp ON sss.set_id = cp.set_id
        WHERE cp.set_id IS NULL
        ORDER BY sss.cache_score DESC
    """
    rows = []
    if limit and isinstance(limit, int) and limit > 0:
        query += " LIMIT $1"
        rows = await _pool.fetch(query, limit)
    else:  
        rows = await _pool.fetch(query)
    
    # Returns a simple list of short_name strings
    return [row['short_name'] for row in rows]

async def get_cache_info() -> Tuple[int, Optional[asyncpg.Record]]:
    """
    Gets the current number of items in the cache and the item with the lowest score.
    Returns a tuple: (current_cache_size, lowest_score_item_row).
    WARNING: make sure to json.loads() when acessing message_ids
    """
    async with _pool.acquire() as conn, conn.transaction():
        count = await conn.fetchval("SELECT COUNT(*) FROM cached_packs")

        lowest_item = None
        if count > 0:
            lowest_item = await conn.fetchrow("SELECT * FROM cached_packs ORDER BY cache_score ASC LIMIT 1")
            
    return count, lowest_item


async def revert_cache_removal_and_log_junk(channel_id: int, message_ids: List[int], set_id: int, reason: str):
    """
    Corrects the file count after a failed deletion and logs the messages as junk.
    This is the reversal for the optimistic decrement in remove_from_cache.
    """
    async with _pool.acquire() as conn, conn.transaction():
        # Reincrement the file count because the files still exist
        await conn.execute(
            "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
            len(message_ids), channel_id
        )

        # Log the junk files for manual cleanup
        now = utcnow()
        records_to_insert = [
            (channel_id, msg_id, set_id, reason, now) for msg_id in message_ids
        ]
        await conn.executemany("""
            INSERT INTO junk_files (channel_id, message_id, set_id, reason, logged_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (channel_id, message_id) DO NOTHING
        """, records_to_insert)
    logger.warning(f"Logged {len(message_ids)} junk files for set {set_id} in channel {channel_id}.")


async def get_all_junk_files_grouped() -> List[asyncpg.Record]:
    """Retrieves all junk files, grouped by their channel ID."""
    return await _pool.fetch("""
        SELECT channel_id, array_agg(message_id ORDER BY message_id ASC) as message_ids
        FROM junk_files
        GROUP BY channel_id
        ORDER BY channel_id
    """)

async def clear_junk_file_entries() -> int:
    """
    Removes all entries from the junk_files table and returns the count of removed entries.
    This should be called after manual deletion of the files.
    """
    deleted_rows = await _pool.fetch("DELETE FROM junk_files RETURNING 1")
    return len(deleted_rows)

async def calculate_and_store_popular_packs():
    """
    Calculates the top 10 daily and top 50 all-time packs and stores them.
    This is intended to be run once daily.
    """
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    async with _pool.acquire() as conn, conn.transaction():   
        # Start fresh
        await conn.execute("DELETE FROM popular_packs")

        # --- Calculate Daily Top 10 (from yesterday's data) ---
        
        # This query joins conversion logs with pack stats to get titles
        daily_packs = await conn.fetch("""
            SELECT
                sss.pack_title,
                cl.pack_url,
                COUNT(cl.set_id) as request_count
            FROM conversion_log cl
            JOIN sticker_set_stats sss ON cl.set_id = sss.set_id
            WHERE cl.request_time >= $1 AND cl.request_time < $2
            GROUP BY sss.pack_title, cl.pack_url, cl.set_id
            ORDER BY request_count DESC
            LIMIT 10;
        """, yesterday_start, today_start)

        daily_inserts = [(i, pack["pack_title"], pack["pack_url"], now) for i, pack in enumerate(daily_packs,1)]
        
        await conn.executemany(
            "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES ('daily', $1, $2, $3, $4)",
            daily_inserts
        )

        # --- Calculate All-Time Top 50 ---
        all_time_packs = await conn.fetch("""
            SELECT pack_title, short_name, is_emoji FROM sticker_set_stats ORDER BY request_count DESC LIMIT 50
        """)
        
        all_time_inserts = []
        for i, pack in enumerate(all_time_packs, 1):
            pack_type_url = "addemoji" if pack['is_emoji'] else "addstickers"
            pack_url = f"https://t.me/{pack_type_url}/{pack['short_name']}"
            all_time_inserts.append((i, pack['pack_title'], pack_url, now))

        await conn.executemany(
            "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES ('all_time', $1, $2, $3, $4)",
            all_time_inserts
        )


async def get_popular_packs(list_type: str) -> List[asyncpg.Record]:
    """Retrieves a pre-calculated list of popular packs."""
    return await _pool.fetch(
            "SELECT pack_title, pack_url FROM popular_packs WHERE list_type = $1 ORDER BY rank ASC",
            list_type)

# A unique integer for the lock. It can be any 64-bit integer.
BOT_ADVISORY_LOCK_ID = 4206942069

# The name of the notification channel.
BOT_HA_CHANNEL = "bot_instance_switchover"


class HighAvailabilityManager:
    """Manages the advisory lock for ensuring only one bot instance is active."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._conn = None # We'll hold a dedicated connection for the lock
        self._lock_acquired = False
        logger.info("High Availability Manager initialized.")

    async def acquire_lock(self) -> bool:
        """
        Tries to acquire the global advisory lock.
        Returns True if successful, False otherwise.
        """
        logger.info("Attempting to acquire the advisory lock...")
        self._conn = await self.pool.acquire()
        # pg_try_advisory_lock is non-blocking. It's perfect for this!
        self._lock_acquired = await self._conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", BOT_ADVISORY_LOCK_ID
        )
        if self._lock_acquired:
            logger.info("👑 Advisory lock acquired! This instance is now the LEADER.")
            return True
        else:
            logger.info("😴 Lock is held by another instance. This instance will be a STANDBY.")
            # We didn't get the lock, so we can release this connection for now.
            await self.pool.release(self._conn)
            self._conn = None
            return False

    async def listen_for_release(self):
        """Waits for a notification that the lock has been released."""
        async with self.pool.acquire() as conn:
            logger.info(f"Listening on channel '{BOT_HA_CHANNEL}' for switchover signal...")
            await conn.add_listener(BOT_HA_CHANNEL, self._notification_handler)
            # This will wait forever until the notification is received.
            # We'll use an asyncio.Event to signal completion.
            self._notification_event = asyncio.Event()
            await self._notification_event.wait()
            logger.info("Switchover signal received!")
            await conn.remove_listener(BOT_HA_CHANNEL, self._notification_handler)

    def _notification_handler(self, connection, pid, channel, payload):
        """Callback function for when a notification is received."""
        logger.info(f"Received NOTIFY on channel '{channel}'. Waking up...")
        self._notification_event.set()

    async def release_and_notify(self):
        """Notifies standby instances and releases the lock."""
        if not self._lock_acquired or not self._conn:
            logger.debug("Tried to release a lock that wasn't held.")
            return

        logger.info(f"Notifying channel '{BOT_HA_CHANNEL}' of imminent shutdown...")
        try:
            # We use a separate connection to NOTIFY so it's not tied to the lock
            async with self.pool.acquire() as notify_conn:
                await notify_conn.execute(f"NOTIFY {BOT_HA_CHANNEL}")
            logger.info(f"Notified channel '{BOT_HA_CHANNEL}' successfully.")
            logger.info("Releasing advisory lock...")
            await self._conn.fetchval(
                "SELECT pg_advisory_unlock($1)", BOT_ADVISORY_LOCK_ID
            )
            self._lock_acquired = False
            logger.info("Lock released successfully.")
        except Exception as e:
            logger.critical(f"CRITICAL: FAILED to release advisory lock or notify! Manual intervention may be needed. Error: {e}")
        finally:
            if self._conn:
                await self.pool.release(self._conn)
                self._conn = None
