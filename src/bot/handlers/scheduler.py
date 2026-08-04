import asyncio
import logging
from datetime import datetime, timezone, timedelta

from src.bot.handlers.context import BotContext
from src.db import calculate_and_store_popular_packs

logger = logging.getLogger(__name__)

class SchedulerLoops:   
    def __init__(self, ctx: BotContext):
        self.ctx = ctx
    async def daily_backup_loop(self):
        """Periodically runs the backup manager task at midnight."""
        while True:
            try:
                now = datetime.now(timezone.utc)
                # target for 23:59:00 UTC today
                next_run = now.replace(hour=23, minute=59, second=0, microsecond=0)
                
                # if the time is already past 23:59 then set it to 23:59 of the next day
                if now >= next_run:
                    next_run += timedelta(days=1)
                
                sleep_seconds = (next_run - now).total_seconds()

                logger.info(f"Next daily backup scheduled in {(sleep_seconds / 3600):.2f} hours.")
                await asyncio.sleep(sleep_seconds)

                logger.info("Starting scheduled daily backup...")
                await self.ctx.backup_manager.run_backup()

            except Exception as e:
                logger.error(f"FATAL: The daily_backup_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)

    async def calculate_popular_packs_loop(self):
        """Periodically calculates and stores popular packs."""
        # Run once on startup to ensure data is available immediately
        try:
            await calculate_and_store_popular_packs()
            await self.ctx.helpers.refresh_popular_packs_cache()
        except Exception as e:
            logger.error(f"Initial popular packs calculation failed: {e}")

        while True:
            try:
                now = datetime.now(timezone.utc)
                next_run = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
                sleep_seconds = (next_run - now).total_seconds()
                
                await asyncio.sleep(sleep_seconds)
                
                # Run the blocking DB function in a separate thread
                await calculate_and_store_popular_packs()
                await self.ctx.helpers.refresh_popular_packs_cache()
                logger.info(f"Popular packs refreshed.")

            except Exception as e:
                logger.error(f"FATAL: The calculate_popular_packs_loop crashed: {e}", exc_info=True)
                await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )
                await asyncio.sleep(3600)
