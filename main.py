"""
Main entry point for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import logging
import asyncio
import os
from telethon import TelegramClient

from notification_manager import NotificationManager
from config import API_ID, API_HASH, BOT_TOKEN, DATA_DIR
from database import init_pool, close_pool, init_db


# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)
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

async def main():
    """
    Initializes the Telethon client, registers handlers, and runs the bot.
    """
    global notification_manager_instance
    os.makedirs(DATA_DIR, exist_ok=True)

    #initialise the dabase connection pool of postgres
    await init_pool()

    # Dont move these imports to top or it'll crash
    # as QueueManager and SessionManager create their global instances queue_manager and session_manager respectively
    # which call get_pool and that would crash if init_pool haven't been called yet.
    # So we cant import them when init_pool havent been called yet.
    # BotHandlers directly import global instance of both so we cant impot BotHandlers either 
    from bot_handlers import BotHandlers
    from session_manager import session_manager

    # We use a session name for the bot so it can remember its state.
    # The session file will be created in the DATA_DIR directory.
    client = TelegramClient(f'{DATA_DIR}/bot_session', API_ID, API_HASH)


    logger.info("Starting bot...")
    try:
        # Initilize the database
        await init_db()

        # Start the client with the bot token
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

        # The bot will run until you press Ctrl+C
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Failed to start or run the bot: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()
        logger.info("Bot stopped.")
        await close_pool()


if __name__ == "__main__":
    try:
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
        

