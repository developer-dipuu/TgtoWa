"""
Queue management system for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from telethon import events

logger = logging.getLogger(__name__)
# Priority Levels
SYSTEM_PRIORITY = 3
REGULAR_USER_PRIORITY = 2
PREMIUM_USER_PRIORITY = 1

@dataclass
class QueueItem:
    user_id: int
    username: str
    bot_reply_message_id: int
    sticker_set: Any
    estimated_seconds: float
    log_id: int
    timestamp: datetime
    priority: int
    event: events.NewMessage.Event
    is_cache_suspicious: bool = False
    is_silent_mode: bool = False
    status: str = "waiting"  # waiting, processing, completed, error

    custom_title: Optional[str] = None
    custom_author: Optional[str] = None


class QueueManager:
    def __init__(self):
        self.queue: List[QueueItem] = []
        self.processing: Optional[QueueItem] = None
        self.user_queues: Dict[int, List[QueueItem]] = {}  # user_id -> QueueItem
        self.queued_set_ids: set[int] = set()
        self._lock = asyncio.Lock()
    
    async def add_to_queue(self, user_id: int, username: str, bot_reply_message_id: int,sticker_set: Any, 
                           estimated_seconds: float, log_id: int, priority: int, event: events.NewMessage.Event, 
                           is_cache_suspicious: bool, is_silent_mode: bool = False, 
                            custom_title: Optional[str] = None, custom_author: Optional[str] = None) -> int:
        """Add user to queue and return position"""
        async with self._lock:
            
            queue_item = QueueItem(
                user_id=user_id,
                username=username,
                bot_reply_message_id=bot_reply_message_id,
                sticker_set=sticker_set,
                estimated_seconds=estimated_seconds,
                log_id=log_id,
                timestamp=datetime.now(timezone.utc),
                priority=priority,
                event=event,
                is_cache_suspicious=is_cache_suspicious,
                is_silent_mode=is_silent_mode,
                custom_title=custom_title,
                custom_author=custom_author
            )

            # Add to the user specific tracking list
            if user_id not in self.user_queues:
                self.user_queues[user_id] = []
            self.user_queues[user_id].append(queue_item)

            # Insert into the main queue based on priority.
            insert_at = len(self.queue)
            for i, existing_item in enumerate(self.queue):
                if existing_item.priority > queue_item.priority:
                    insert_at = i
                    break
            self.queue.insert(insert_at, queue_item)
            
            # add the item's set_id
            self.queued_set_ids.add(queue_item.sticker_set.set.id)

            priority_map = {SYSTEM_PRIORITY: 'system', REGULAR_USER_PRIORITY: 'regular', PREMIUM_USER_PRIORITY: 'premium'}
            priority_str = priority_map.get(priority, 'unknown')
            logger.info(f"Added {priority_str} user {username} (ID: {user_id}) to queue for pack: {sticker_set.set.short_name}")
            
            # Return the position of the newly added item
            return self.get_queue_position(user_id, specific_item=queue_item)
            
    async def cancel_item(self, user_id: int, log_id: int) -> bool:
        """Removes a specific item from the queue by its log_id."""
        async with self._lock:
            # Find the item in the main queue
            item_to_remove = next((item for item in self.queue if item.log_id == log_id and item.user_id == user_id), None)

            if item_to_remove:
                # Remove from the main queue
                self.queue.remove(item_to_remove)
                # remove the item's set_id
                self.queued_set_ids.discard(item_to_remove.sticker_set.set.id)
                # Remove from the user specific queue
                user_specific_queue = self.user_queues.get(user_id, [])
                if item_to_remove in user_specific_queue:
                    user_specific_queue.remove(item_to_remove)

                if not user_specific_queue:
                    del self.user_queues[user_id]
                
                logger.info(f"User {user_id} cancelled item with log_id {log_id}")
                return True
        return False


    def is_set_id_queued(self, set_id: int) -> bool:
        """
        Efficiently checks if a set_id is either being processed or waiting in the queue.
        This is an O(1) operation.
        """
        return set_id in self.queued_set_ids
    

    async def get_next_item(self) -> Optional[QueueItem]:
        """Get next item to process"""
        async with self._lock:
            if self.processing is not None:
                return None
            
            if not self.queue:
                return None
            
            item = self.queue.pop(0)
            item.status = "processing"
            self.processing = item
            
            logger.info(f"Starting processing for user {item.username} (ID: {item.user_id})")
            return item
        
    
    async def complete_processing(self, user_id: int, success: bool = True):
        """Mark current processing as complete"""
        async with self._lock:
            if self.processing and self.processing.user_id == user_id:
                # remove the set_id from our tracking set
                self.queued_set_ids.discard(self.processing.sticker_set.set.id)

                self.processing.status = "completed" if success else "error"
                
                # Remove the completed item from the user's list
                user_specific_queue = self.user_queues.get(user_id, [])
                if self.processing in user_specific_queue:
                    user_specific_queue.remove(self.processing)
                
                # If the user has no more items, remove them from the dict
                if not user_specific_queue:
                    del self.user_queues[user_id]
                
                logger.info(f"Completed processing for user {self.processing.username} (ID: {user_id}), success: {success}")
                self.processing = None
    
    def get_queue_position(self, user_id: int, specific_item: Optional[QueueItem] = None) -> int:
        """
        Get user's best position in queue.
        If specific_item is provided, it gets the position of that exact item.
        """
        user_items = self.user_queues.get(user_id, [])
        if not user_items:
            return 0
        # check for item if provided ortherwise their first item in queue
        item_to_find = specific_item or user_items[0]

        try:
            # Position is 1-based index in queue + 1 if someone is processing
            processing_offset = 1 if self.processing else 0
            return self.queue.index(item_to_find) + 1 + processing_offset
        except ValueError:
            # Item is not in the waiting queue, it might be processing
            return 1 if self.processing and self.processing.user_id == user_id else 0
    
    def get_queue_stats(self) -> dict:
        """Get queue statistics"""
        return {
            "total_waiting": len(self.queue),
            "currently_processing": self.processing is not None,
            "processing_user": self.processing.username if self.processing else None
        }
    
    async def get_user_queue_count(self, user_id: int) -> int:
        """Check how many items a user has in the queue (waiting or processing)."""
        async with self._lock:
            count = len(self.user_queues.get(user_id, []))
            return count

# Global queue manager instance
queue_manager = QueueManager()
