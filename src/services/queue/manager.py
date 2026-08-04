"""
A persistent queue management system backed by PostgreSQL.
"""

import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import asyncpg

from src.db import get_pool

logger = logging.getLogger(__name__)

# Priority Levels (lower == greater)
SYSTEM_PRIORITY = 3
REGULAR_USER_PRIORITY = 2
PREMIUM_USER_PRIORITY = 1

@dataclass
class QueueItem:
    """
    Represents a queue item retrieved from the database.
    This structure is kept consistent to minimize changes in bot_handlers.py.
    """
    id: int  # The database primary key from the 'queue' table
    user_id: int
    chat_id: int
    priority: int
    status: str
    log_id: int
    
    # All other data is dynamically extracted from the item_data JSONB field
    # We use field(init=False) so they don't need to be passed to the constructor
    message_id: int = field(init=False)
    username: str = field(init=False)
    bot_reply_message_id: int = field(init=False)
    sticker_set_info: dict = field(init=False)
    estimated_seconds: float = field(init=False)
    is_cache_suspicious: bool = field(init=False)
    is_silent_mode: bool = field(init=False)
    custom_title: Optional[str] = field(init=False)
    custom_author: Optional[str] = field(init=False)

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> 'QueueItem':
        """Creates a QueueItem instance from a database row."""
        item_data = json.loads(row['item_data'])
        
        # Create the base instance
        instance = cls(
            id=row['id'],
            user_id=row['user_id'],
            chat_id=row['chat_id'],
            priority=row['priority'],
            status=row['status'],
            log_id=row['log_id']
        )

        # Populate the dynamic fields from the JSONB data
        instance.message_id = item_data.get('message_id')
        instance.username = item_data.get('username')
        instance.bot_reply_message_id = item_data.get('bot_reply_message_id')
        instance.sticker_set_info = item_data.get('sticker_set_info')
        instance.estimated_seconds = item_data.get('estimated_seconds')
        instance.is_cache_suspicious = item_data.get('is_cache_suspicious', False)
        instance.is_silent_mode = item_data.get('is_silent_mode', False)
        instance.custom_title = item_data.get('custom_title')
        instance.custom_author = item_data.get('custom_author')
        
        return instance

class QueueManager:
    def __init__(self):
        """
        The QueueManager is now stateless. All state is managed in the database.
        The in-memory queue, locks, and dictionaries have been removed.
        """
        self.pool = get_pool()

    async def add_to_queue(self, user_id: int, chat_id: int, message_id: int, username: str,
                           bot_reply_message_id: int, sticker_set_info: dict,
                           estimated_seconds: float, log_id: int, priority: int,
                           is_cache_suspicious: bool, is_silent_mode: bool = False,
                           custom_title: Optional[str] = None, custom_author: Optional[str] = None) -> int:
        """Adds a new item to the queue by inserting a row into the database."""
        item_data = {
            "message_id": message_id,
            "username": username,
            "bot_reply_message_id": bot_reply_message_id,
            "sticker_set_info": sticker_set_info,
            "estimated_seconds": estimated_seconds,
            "is_cache_suspicious": is_cache_suspicious,
            "is_silent_mode": is_silent_mode,
            "custom_title": custom_title,
            "custom_author": custom_author
        }
        
        # Convert the dictionary to a JSON string for the database
        item_data_json = json.dumps(item_data)
        set_id = sticker_set_info.get('set_id') # for fater checkings 

        await self.pool.execute(
            """
            INSERT INTO queue (user_id, chat_id, set_id, priority, log_id, item_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id, chat_id, set_id, priority, log_id, item_data_json
        )

        priority_str = {1: 'premium', 2: 'regular', 3: 'system'}.get(priority, 'unknown')
        logger.info(f"Added {priority_str} user {username} (ID: {user_id}) to queue for pack: {sticker_set_info['short_name']}")

        # Return the user's new position in the queue
        return await self.get_queue_position(user_id, log_id=log_id)

    async def cancel_item(self, user_id: int, log_id: int) -> bool:
        """Cancels a 'waiting' item by updating its status in the database."""
        row = await self.pool.fetchval(
            "UPDATE queue SET status = 'cancelled' WHERE log_id = $1 AND status = 'waiting' RETURNING 1",
            log_id
        )
        if row:
            logger.info(f"User {user_id} cancelled item with log_id {log_id}")
            return True
        return False

    async def is_set_id_queued(self, set_id: int) -> bool:
        """Checks if a sticker set is currently 'waiting' or 'processing' in the queue."""
        row = await self.pool.fetchrow(
            "SELECT 1 FROM queue WHERE set_id = $1 AND status IN ('waiting', 'processing')",
            set_id
        )
        return row is not None

    async def get_next_item(self) -> QueueItem | None:
        """
        Atomically fetches and locks the next available item from the queue,
        updating its status to 'processing'.
        """
        row = await self.pool.fetchrow("""
            UPDATE queue
            SET status = 'processing', processing_started_at = NOW()
            WHERE id = (
                SELECT id
                FROM queue
                WHERE status = 'waiting'
                ORDER BY priority ASC, added_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *;
        """)

        if row:
            item = QueueItem.from_row(row)
            logger.info(f"Starting processing for user {item.username} (ID: {item.user_id}) from queue.")
            return item
        return None

    async def complete_processing(self, item_id: int, success: bool):
        """Marks a processing item as 'completed' or 'failed'."""
        final_status = 'completed' if success else 'failed'
        await self.pool.execute(
            "UPDATE queue SET status = $1 WHERE id = $2",
            final_status, item_id
        )
        logger.info(f"Completed processing for queue item {item_id}, success: {success}")

    async def get_queue_position(self, user_id: int, log_id: int | None = None) -> int:
        """Calculates a user's specific item's position in the queue."""
        async with self.pool.acquire() as conn, conn.transaction():
            # FIRST, check if this user has an item being processed.
            currently_processing = await conn.fetchrow(
                "SELECT user_id FROM queue WHERE status = 'processing' LIMIT 1",
            )
            if currently_processing and currently_processing['user_id'] == user_id:
                return -1
            
            # If a log_id is provided, find that specific item.
            if log_id:
                user_item = await conn.fetchrow(
                    "SELECT priority, added_at FROM queue WHERE log_id = $1",
                    log_id
                )
            # Otherwise, find the user's nearest 'waiting' item.
            else:
                user_item = await conn.fetchrow(
                    """
                    SELECT priority, added_at FROM queue 
                    WHERE user_id = $1 AND status = 'waiting' 
                    ORDER BY priority ASC, added_at ASC LIMIT 1
                    """,
                    user_id
                )
            
            if not user_item:
                return 0 # Item not found
            
            # Now, count how many waiting items are "better" than requested one (higher priority or same priority but added earlier)
            position = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM queue
                WHERE status = 'waiting' AND (
                    priority < $1 OR (priority = $1 AND added_at <= $2)
                )
                """,
                user_item['priority'], user_item['added_at']
            )
            
            is_processing = currently_processing is not None
            return position + (1 if is_processing else 0) # 1 based position

    async def get_queue_stats(self) -> dict:
        """Gets overall queue statistics from the database."""
        stats = await self.pool.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM queue WHERE status = 'waiting') as total_waiting,
                (SELECT item_data->>'username' FROM queue WHERE status = 'processing' LIMIT 1) as processing_user
            """
        )
        if not stats:
            return {"total_waiting": 0, "currently_processing": False, "processing_user": None}

        return {
            "total_waiting": stats["total_waiting"],
            "currently_processing": stats["processing_user"] is not None,
            "processing_user": stats["processing_user"]
        }

    async def get_user_queue_count(self, user_id: int) -> int:
        """Counts how many items a user has that are 'waiting' or 'processing'."""
        count = await self.pool.fetchval(
            "SELECT COUNT(*) FROM queue WHERE user_id = $1 AND status IN ('waiting', 'processing')",
            user_id,
        )
        return count or 0
    
    async def cleanup_queue(self) -> int:
        """
        Deletes old, finished (completed, failed, cancelled) items from the queue table.
        """
        # We'll delete anything finished more than 12 hours ago
        delete_threshold = datetime.now(timezone.utc) - timedelta(hours=12)

        # We use COALESCE to check the processing time first, but fall back to the
        # creation time for items that were cancelled before processing began
        deleted_rows = await self.pool.fetch(
            """
            DELETE FROM queue
            WHERE status IN ('completed', 'failed', 'cancelled')
            AND COALESCE(processing_started_at, added_at) < $1
            RETURNING id
            """,
            delete_threshold
        )

        deleted_count = len(deleted_rows)
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old, finished item(s) from the queue.")
        
        return deleted_count


    async def requeue_stale_items(self) -> int:
        """
        Finds any items stuck in 'processing' (e.g. from a crash or forced shutdown)
        and resets them to 'waiting' to be picked up again.
        We give them a slightly elevated priority to get them done first.
        """
        stale_items = await self.pool.fetch(
            """
            UPDATE queue
            SET status = 'waiting',
                priority = priority - 1,
                processing_started_at = NULL
            WHERE status = 'processing'
            RETURNING id, user_id
            """
        )

        count = len(stale_items)
        if count > 0:
            user_ids = [item['user_id'] for item in stale_items]
            logger.warning(
                f"Found and re-queued {count} stale item(s) from a previous session for users: {user_ids}."
            )
        return count
# Global queue manager instance
queue_manager = QueueManager()