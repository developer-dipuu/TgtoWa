import asyncio

from src.bot.handlers.context import BotContext
from src.bot.handlers.cleanup import CleanupLoops
from src.bot.handlers.scheduler import SchedulerLoops

class BackGroundTask:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx
        self.cleanup = CleanupLoops(self.ctx)
        self.scheduler = SchedulerLoops(self.ctx)
        
    def start(self):
        #background tasks for cleanup
        asyncio.create_task(self.cleanup.reply_locks_cleanup_loop(ttl_seconds=3600))
        asyncio.create_task(self.cleanup.db_cleanup_loop())
        asyncio.create_task(self.cleanup.premium_users_cleanup_loop(check_interval_seconds=86400))
        asyncio.create_task(self.cleanup.callback_locks_cleanup_loop(ttl_seconds=3600, check_interval_seconds=600))
        asyncio.create_task(self.scheduler.calculate_popular_packs_loop())
        asyncio.create_task(self.scheduler.daily_backup_loop())