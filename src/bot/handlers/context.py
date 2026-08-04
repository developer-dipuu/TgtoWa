import asyncio
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from telethon import TelegramClient

if TYPE_CHECKING:
    from src.bot.handlers.admin import AdminCommands
    from src.bot.handlers.owner import OwnerCommands
    from src.bot.handlers.user import UserCommands
    from src.bot.handlers.session import SessionHandler
    from src.bot.handlers.template import TemplateHelper
    from src.bot.handlers.helper import HelperMethods
    from src.bot.handlers.core import BotHandlers

@dataclass
class BotContext:
    """Single source of truth for all shared bot state."""

    # immutable (set once at init)
    client: TelegramClient
    notification_manager: object   # NotificationManager
    payment_manager: object        # PaymentManager
    converter: object              # StickerConverter
    network_task: object           # NetworkTask
    backup_manager: object         # BackupManager
    bot_username: str

    # constants
    START_MESSAGE: str = ""
    START_BUTTONS: list = field(default_factory=list)

    # Mutable Shared State
    cache_enabled: bool = True
    cache_full_notified: bool = False
    shutting_down: bool = False
    daily_popular_packs: list = field(default_factory=list)
    all_time_popular_packs: list = field(default_factory=list)
    pending_actions: dict = field(default_factory=dict)

    # owner job trackers
    active_refresh_jobs: set = field(default_factory=set)
    active_refresh_message: object = None   # Message | None
    active_add_jobs: set = field(default_factory=set)
    active_add_message: object = None       # Message | None

    # concurrency locks
    processing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    user_callback_locks: dict = field(default_factory=dict)
    user_callback_locks_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    user_processing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users_adding_to_queue: set = field(default_factory=set)
    reply_locks: dict = field(default_factory=dict)
    reply_locks_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # references
    core: Optional['BotHandlers'] = None
    admin: Optional['AdminCommands'] = None
    owner: Optional['OwnerCommands'] = None
    user: Optional['UserCommands'] = None
    sessions: Optional['SessionHandler'] = None
    templates: Optional['TemplateHelper'] = None
    helpers: Optional['HelperMethods'] = None
