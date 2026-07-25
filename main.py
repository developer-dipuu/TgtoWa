"""
Main entry point for the bot.
"""

import logging
import asyncio
import os
import signal
from telethon import TelegramClient

from notification_manager import NotificationManager
from config import API_ID, API_HASH, BOT_TOKEN, DATA_DIR
from database import init_pool, close_pool, get_pool, init_db


# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)
ha_manager = None
shutdown_signals = 0
shutdown_task = None
notification_manager_instance: NotificationManager = None

def handle_exception(loop, context):
    """Global exception handler for the asyncio loop to catch unhandled errors."""
    exc = context.get("exception", context["message"])
    logger.critical(f"Caught an unhandled exception in a task: {exc}", exc_info=exc)
    
    if notification_manager_instance:
        # Create a coroutine to send the notification via our manager
        coro = notification_manager_instance.send_uncaught_exception(
            (type(exc), exc, exc.__traceback__)
        )
        # Schedule the coroutine to run safely on the loop
        asyncio.run_coroutine_threadsafe(coro, loop)

async def handle_shutdown_signal(handlers: 'BotHandlers', client: 'TelegramClient'): # type: ignore
    """
    Handles the shutdown logic based on how many Ctrl+C signals are received.
    """
    global shutdown_signals
    shutdown_signals += 1
    
    if shutdown_signals == 1:
        logger.warning("⚠️ Graceful shutdown initiated (1/2). Finishing current task... ⚠️")
        # Signal the queue processor to stop picking up new items
        handlers.shutting_down = True
        
        # Wait for the current item to finish processing
        async with handlers.processing_lock:
            logger.warning("Current task finished. Proceeding with shutdown.")
            # Now we can safely shut down everything else
            if ha_manager:
                await ha_manager.release_and_notify()
            if client.is_connected():
                await client.disconnect()
            await close_pool()
            logger.info("Bot shutdown complete.")

    elif shutdown_signals >= 2:
        logger.critical("🚨 IMMEDIATE SHUTDOWN INITIATED (2/2) 🚨")
        # Don't wait, just release the lock and notify immediately
        if ha_manager:
            await ha_manager.release_and_notify()
        if client.is_connected():
            await client.disconnect()
        await close_pool()
        exit(1)


async def main():
    """
    Initializes the Telethon client, registers handlers, and runs the bot.
    """
    global notification_manager_instance, ha_manager, shutdown_task
    os.makedirs(DATA_DIR, exist_ok=True)

    #initialise the dabase connection pool of postgres
    await init_pool()
    # Initialize the HA manager
    from database import HighAvailabilityManager
    ha_manager = HighAvailabilityManager(get_pool())

    # Loop to acquire lock or listen
    while True:
        if await ha_manager.acquire_lock():
            break # We are the leader, proceed!
        else:
            await ha_manager.listen_for_release()
            # After notification, loop back to try acquiring the lock again.
            await asyncio.sleep(1) # Small delay to prevent frantic retries


    # ====== From this point, we are the confirmed LEADER instance =========


    # Dont move these imports to top or it'll crash
    # as QueueManager and SessionManager create their global instances queue_manager and session_manager respectively
    # which call get_pool and that would crash if init_pool haven't been called yet.
    # So we cant import them when init_pool havent been called yet.
    # BotHandlers directly import global instance of both so we cant impot BotHandlers either
    # Moreover we should only start these when we are confirmed to be LEADER instance
    from bot_handlers import BotHandlers
    from session_manager import session_manager
    from queue_manager import queue_manager

    # We use a session name for the bot so it can remember its state.
    # The session file will be created in the DATA_DIR directory.
    client = TelegramClient(f'{DATA_DIR}/bot_session', API_ID, API_HASH)


    logger.info("Starting bot...")
    try:
        # Initilize the database
        await init_db()

        # requeue 
        requeued_count = await queue_manager.requeue_stale_items()
        # Start the client with the bot token
        # pyrefly: ignore [not-async]
        await client.start(bot_token=BOT_TOKEN)

        # Rebuild the session index right after starting and before handling events
        await session_manager.rebuild_msg_index()

        # Initialize NotificationManager AFTER client starts and is ready
        notification_manager_instance = NotificationManager(client)
        
        # Set the custom exception handler for the currently running asyncio loop
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(handle_exception)

        bot_info = await client.get_me()
        logger.info(f"Bot started successfully as @{bot_info.username}!")

        # Initialize handlers with the client instance and bot_info
        handlers = BotHandlers(client, bot_info, notification_manager_instance)
        # Register all event handlers
        handlers.register_handlers()

        # Start processing the queue if there's anything left over
        if requeued_count > 0 or (await queue_manager.get_queue_stats())['total_waiting'] > 0:
            if not handlers.processing_lock.locked():
                logger.info("Tasks found in queue on startup, initiating queue processing.")
                asyncio.create_task(handlers.process_queue())
        
        # Setup the signal handler for Ctrl+C
        # We create a task because the signal handler itself cannot be async
        def signal_handler_wrapper():
            global shutdown_task
            if not shutdown_task or shutdown_task.done():
                 shutdown_task = asyncio.create_task(handle_shutdown_signal(handlers, client))

        loop.add_signal_handler(signal.SIGINT, signal_handler_wrapper)

        # The bot will run until you press Ctrl+C
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Failed to start or run the bot: {e}", exc_info=True)
    finally:
        logger.info("Bot stopping... ensuring resources are released.")
        if ha_manager:
            await ha_manager.release_and_notify()
        if client and client.is_connected():
            await client.disconnect()
        await close_pool()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        # Run the main async function
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutdown requested by user.")
        

