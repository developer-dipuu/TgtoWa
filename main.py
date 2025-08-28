"""
Main entry point for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import logging
import asyncio
import os
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, DATA_DIR
from bot_handlers import BotHandlers
from database import init_db
from notification_manager import NotificationManager

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
    # Initilize the database
    init_db()
    # We use a session name for the bot so it can remember its state.
    # The session file will be created in the DATA_DIR directory.
    client = TelegramClient(f'{DATA_DIR}/bot_session', API_ID, API_HASH)


    logger.info("Starting bot...")
    try:
        # Start the client with the bot token
        await client.start(bot_token=BOT_TOKEN)

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


if __name__ == "__main__":
    try:
        # Run the main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
        

