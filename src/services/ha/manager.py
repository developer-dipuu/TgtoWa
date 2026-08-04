import asyncio
import asyncpg
import logging

from src.db.pool import get_pool

logger = logging.getLogger(__name__)

# A unique integer for the lock. It can be any 64-bit integer.
BOT_ADVISORY_LOCK_ID = 4206942069

# The name of the notification channel.
BOT_HA_CHANNEL = "bot_instance_switchover"


class HighAvailabilityManager:
    """Manages the advisory lock for ensuring only one bot instance is active."""

    def __init__(self):
        self.pool = get_pool()
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

# global instance
high_availability_manager = HighAvailabilityManager()
