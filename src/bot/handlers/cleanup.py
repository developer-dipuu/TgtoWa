import asyncio
from datetime import datetime, timezone
import logging

from src.db import remove_expired_premium_users
from src.services.sessions.manager import session_manager
from src.services.queue.manager import queue_manager
from src.bot.handlers.context import BotContext

logger = logging.getLogger(__name__)

class CleanupLoops:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx

    async def callback_locks_cleanup_loop(self, ttl_seconds: int = 3600, check_interval_seconds: int = 600):
        """Periodically clean old user callback locks to prevent memory growth."""
        while True:
            await asyncio.sleep(check_interval_seconds)
            try:
                now = datetime.now(timezone.utc)
                async with self.ctx.user_callback_locks_lock:
                    to_remove = []
                    for user_id, entry in self.ctx.user_callback_locks.items():
                        lock = entry.get("lock")
                        last_used = entry.get("last_used", now)
                        # Only remove if it's unlocked and hasn't been used for a while
                        if not lock.locked() and (now - last_used).total_seconds() > ttl_seconds:
                            to_remove.append(user_id)
                    
                    for user_id in to_remove:
                        self.ctx.user_callback_locks.pop(user_id, None)
                        logger.debug(f"Cleaned up callback lock for user {user_id}")

            except Exception as e:
                logger.error(f"FATAL: The callback_locks_cleanup_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600) # time for developer to fix and stop the same error code running again otherwise it will keep sending damn notifications every 10m
            logger.info("Cleaned old user callback locks.")

    async def reply_locks_cleanup_loop(self, ttl_seconds=3600):
        """Periodically clean old reply locks to prevent memory growth.
        Only remove locks that are unlocked AND not used for > ttl_seconds."""
        while True:
            await asyncio.sleep(ttl_seconds)
            logger.info("Cleaning old admin reply locks...")
            try:
                now = datetime.now(timezone.utc)
                async with self.ctx.reply_locks_lock:
                    to_remove = []
                    for cid, entry in list(self.ctx.reply_locks.items()):
                        lock = entry.get("lock")
                        last = entry.get("last_used", now)
                        # remove only if unlocked and idle longer than ttl_seconds
                        if (not lock.locked()) and ((now - last).total_seconds() > ttl_seconds):
                            to_remove.append(cid)
                    for cid in to_remove:
                        self.ctx.reply_locks.pop(cid, None)
            except Exception as e:
                logger.error(f"FATAL: The reply_locks_cleanup_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)
            logger.info("cleaned up old admin locks.")

    async def db_cleanup_loop(self, check_interval_seconds: int = 3600):
        """Periodically cleans up old data from the database."""
        while True:
            await asyncio.sleep(check_interval_seconds)
            try:
                # The managers handle all the cleanup logics
                await session_manager.cleanup()
                await queue_manager.cleanup_queue()
            except Exception as e:
                logger.error(f"FATAL: The db_cleanup_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                # Wait a while before retrying if it crashes
                await asyncio.sleep(3600)

    async def premium_users_cleanup_loop(self, check_interval_seconds: int = 86400):
        """Periodically cleans up expired premium users from the database."""
        while True:
            # Wait for the next interval
            await asyncio.sleep(check_interval_seconds)
            try:
                logger.info("Running scheduled cleanup of expired premium users...")
                # call the database function
                removed_count = await remove_expired_premium_users()
                if removed_count > 0:
                    logger.info(f"SYSTEM: Automatically removed {removed_count} expired premium users.")
                else:
                    logger.info("No expired premium users found to remove.")
            except Exception as e:
                logger.error(f"FATAL: The premium_users_cleanup_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)
