"""
Queue management system for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclass
class QueueItem:
    user_id: int
    username: str
    chat_id: int
    message_id: int
    bot_reply_message_id: int
    pack_input: Any  # Can be a string (short_name) or an InputStickerSet object
    log_id: int
    timestamp: datetime
    is_premium: bool
    status: str = "waiting"  # waiting, processing, completed, error

class QueueManager:
    def __init__(self):
        self.queue: List[QueueItem] = []
        self.processing: Optional[QueueItem] = None
        self.user_queues: Dict[int, List[QueueItem]] = {}  # user_id -> QueueItem
        self._lock = asyncio.Lock()
    
    async def add_to_queue(self, user_id: int, username: str, chat_id: int, 
                          message_id: int, bot_reply_message_id: int, pack_input: Any, log_id: int,  is_premium: bool) -> int:
        """Add user to queue and return position"""
        async with self._lock:
            
            queue_item = QueueItem(
                user_id=user_id,
                username=username,
                chat_id=chat_id,
                message_id=message_id,
                bot_reply_message_id=bot_reply_message_id,
                pack_input=pack_input,
                log_id=log_id,
                timestamp=datetime.now(),
                is_premium=is_premium
            )

            # Add to the user-specific tracking list
            if user_id not in self.user_queues:
                self.user_queues[user_id] = []
            self.user_queues[user_id].append(queue_item)

            # Insert into the main queue with priority for premium
            if is_premium:
                # Find the first non-premium user and insert before them
                insert_at = len(self.queue)
                for i, item in enumerate(self.queue):
                    if not item.is_premium:
                        insert_at = i
                        break
                self.queue.insert(insert_at, queue_item)
            else:
                self.queue.append(queue_item)
            
            logger.info(f"Added {'premium' if is_premium else 'regular'} user {username} (ID: {user_id}) to queue for pack: {pack_input}")
            
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
                
                # Remove from the user-specific queue
                user_specific_queue = self.user_queues.get(user_id, [])
                if item_to_remove in user_specific_queue:
                    user_specific_queue.remove(item_to_remove)

                if not user_specific_queue:
                    del self.user_queues[user_id]
                
                logger.info(f"User {user_id} cancelled item with log_id {log_id}")
                return True
        return False
    
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
