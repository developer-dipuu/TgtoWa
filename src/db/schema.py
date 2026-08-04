import asyncpg
import logging
from src.db.pool import get_pool

logger = logging.getLogger(__name__)

async def init_db():
    """Initializes the database and creates tables if they don't exist."""
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
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
                
                # Create payment_status enum
                await conn.execute("""
                    DO $$ BEGIN
                        CREATE TYPE payment_status AS ENUM ('pending', 'success', 'refunded', 'failed');
                    EXCEPTION
                        WHEN duplicate_object THEN null;
                    END $$;
                """)  

                # For storing payments
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS payments (
                        payment_id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        payment_method VARCHAR(50) DEFAULT 'manual' NOT NULL,
                        transaction_id TEXT UNIQUE,
                        amount INTEGER,
                        currency VARCHAR(10),
                        status payment_status DEFAULT 'pending' NOT NULL, 
                        duration_days INTEGER NOT NULL,
                        is_deducted BOOLEAN DEFAULT FALSE NOT NULL,
                        metadata JSONB DEFAULT '{}' NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """)

                # Create actor_type enum
                await conn.execute("""
                    DO $$ BEGIN
                        CREATE TYPE actor_type AS ENUM ('user', 'admin', 'system');
                    EXCEPTION
                        WHEN duplicate_object THEN null;
                    END $$;
                """)  

                # For storing payment history (logs of payment status changes)
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS payment_history
                (
                    history_id SERIAL PRIMARY KEY,
                    payment_id INTEGER NOT NULL REFERENCES payments(payment_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    previous_status payment_status,
                    new_status payment_status DEFAULT 'pending' NOT NULL, 
                    actor_type actor_type NOT NULL,
                    actor_id BIGINT,
                    old_metadata JSONB,
                    new_metadata JSONB,
                    reason TEXT,
                    action_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """)

                # Create premium_history_action enum
                await conn.execute("""
                    DO $$ BEGIN
                        CREATE TYPE premium_history_action AS ENUM ('added', 'removed', 'extended', 'deducted', 'refunded', 'expired');
                    EXCEPTION
                        WHEN duplicate_object THEN null;
                    END $$;
                """)  


                # premium_history table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS premium_history (
                        history_id SERIAL PRIMARY KEY,
                        admin_id BIGINT NOT NULL,
                        target_user_id BIGINT NOT NULL,
                        payment_id INTEGER REFERENCES payments (payment_id) ON DELETE SET NULL,
                        action premium_history_action NOT NULL, 
                        duration_change_days INTEGER,
                        previous_expiry_date TIMESTAMP WITH TIME ZONE,
                        new_expiry_date TIMESTAMP WITH TIME ZONE,
                        reason TEXT,
                        action_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
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
                        short_name TEXT UNIQUE,
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
                
                # For cheking premium duaration left or managing premium /gstats list and daily premium user cleanup job
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
