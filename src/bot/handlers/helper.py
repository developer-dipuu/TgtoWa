
import html
import math
import asyncio
import logging
from typing import List, Optional, Sequence, Any
from telethon import Button, events
from telethon.events import StopPropagation
from telethon.errors import ChatAdminRequiredError
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.messages import SendReactionRequest, GetCustomEmojiDocumentsRequest
from telethon.errors.rpcerrorlist import UserNotParticipantError, MessageDeleteForbiddenError
from telethon.tl.types import DocumentAttributeSticker, MessageEntityCustomEmoji, DocumentAttributeCustomEmoji, ReactionEmoji, User

from src import db
from src.bot.handlers.context import BotContext
from src.utils.parsers import extract_pack_name_from_url
from src.services.sessions.manager import Flow, Session, session_manager
from src.core.config import REQUIRED_CHANNELS_FORMATTED, DOWNLOAD_TIMEOUT

logger = logging.getLogger(__name__)


class HelperMethods:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx



    async def react(self, event: events.NewMessage.Event| None = None, chat_id: int | None = None, msg_id: int | None = None, emoji: str = "👍", big: bool = False) -> bool:
        if not event and not (chat_id and msg_id):
            raise ValueError("You must provide either an event or both chat_id and msg_id")
        if event:
            chat_id=  event.chat_id
            msg_id = event.message.id
        try:
            await self.ctx.client(SendReactionRequest(
                peer=chat_id,
                big=big,
                msg_id=msg_id,
                reaction=[ReactionEmoji(
                    emoticon=emoji
                )]
            ))
        except Exception as e:
            logger.error(f"An error while reacting to message {msg_id} in chat {chat_id}: {e}")
            return False
        return True

    async def safe_reply(self, event, *args, **kwargs):
        """Safely replies to an event, catching and logging any errors."""
        try:
            return await event.reply(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to reply in background: {e}")
            return None
    
    async def refresh_popular_packs_cache(self):
        """Fetches popular packs from DB and loads them into memory."""
        try:
            self.ctx.daily_popular_packs = await db.get_popular_packs('daily')
            self.ctx.all_time_popular_packs = await db.get_popular_packs('all_time')
            logger.info(f"Popular packs refreshed. Loaded {len(self.ctx.daily_popular_packs)} daily and {len(self.ctx.all_time_popular_packs)} all-time packs.")
        except Exception as e:
            logger.error(f"Failed to refresh in-memory popular packs cache: {e}")
            await self.ctx.notification_manager.send_uncaught_exception(
                    (type(e), e, e.__traceback__)
                )

    async def get_cache_channel(self):
        cache_channel = await db.get_or_create_cache_channel()
        if cache_channel: return cache_channel
        if not self.ctx.cache_full_notified:
            asyncio.create_task(self.ctx.notification_manager.send_cache_full_notification())
            self.ctx.cache_full_notified = True
        return None
    
    async def delete_cache(self, set_id) -> bool | None:
        """
        Attempts to remove a pack from the cache.
        If TG deletion fails (e.g., msg > 48h old), it logs the files as junk.
        Returns:
            - True: Successful deletion from DB and TG.
            - False: Deletion failed from TG, files logged as junk.
            - None: Pack was not found in the cache DB.
        """
        position = await db.remove_from_cache(set_id) 
        if position:
            channel_id, message_ids = position
            try:
                await self.ctx.client.delete_messages(channel_id, message_ids)
            except MessageDeleteForbiddenError as e:
                logger.warning(f"Could not delete messages for set {set_id}. They are > 48h old. Logging as junk.")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, "messages are > 48h old")
                return False
            except ChatAdminRequiredError as e:
                logger.error(f"Could not delete cache messages for set {set_id}. Insufficient permissions in the channel {channel_id}!")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, "insufficient permissions")
                asyncio.create_task(self.ctx.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
            except Exception as e:
                logger.error(f"Could not delete cache messages for set {set_id}. Error: {e}")
                await db.revert_cache_removal_and_log_junk(channel_id, message_ids, set_id, str(e))
                asyncio.create_task(self.ctx.notification_manager.send_cache_delete_failure(channel_id, message_ids, e))
                return False
        else:
            return None
        return True
    
    async def delete_multiple_cache(self, set_ids: List[int]) -> dict:
        """
        Deletes multiple packs from cache by iterating over them.
        Returns a dictionary with counts of success/failure.
        """
        if not set_ids:
            return {"succeeded": 0, "failed": 0, "not_found": 0}

        results = {"succeeded": 0, "failed": 0, "not_found": 0}

        for set_id in set_ids:
            status = await self.delete_cache(set_id)
            if status is True:
                results["succeeded"] += 1
            elif status is False:
                results["failed"] += 1
            else: # None
                results["not_found"] += 1
            await asyncio.sleep(0.5) # to be nice to Telegram API

        return results
    
    async def delete_message(self, chat_id: int, msg_id: int | Sequence[int], custom_error_log: str | None = None):
        """Deletes messages from a chat. Accepts a single message ID or list of message IDs. Returns success."""
        def log_error(base_msg: str):
            full_msg = f"{custom_error_log} Error: {base_msg}" if custom_error_log else base_msg
            logger.error(full_msg)

        try:
            await self.ctx.client.delete_messages(chat_id, msg_id)
        except ChatAdminRequiredError as e:
            log_error(f"Can't delete message(s): {msg_id}. Bot lacks required permissions in chat {chat_id}.")
            if custom_error_log:
                asyncio.create_task(self.ctx.notification_manager.send_message_delete_failure(chat_id, msg_id, custom_error_log, e))
            return False
        except Exception as e:
            log_error(f"Could not delete message(s) {msg_id} in chat {chat_id}. Error: {e}")
            if custom_error_log:
                asyncio.create_task(self.ctx.notification_manager.send_message_delete_failure(chat_id, msg_id, custom_error_log, e))
            return False
        return True
        
    async def delete_multiple_messages(self, chat_id: int, message_ids: List[int], custom_error_log: str | None = None):
        """Deletes bulk messages from a chat with proper waiting to avoid rate limits and with max speed. Accepts a list of message IDs. Returns success."""
        if not message_ids:
            return None
        
        def log_error(base_msg: str):
            full_msg = f"{custom_error_log} Error: {base_msg}" if custom_error_log else base_msg
            logger.error(full_msg)

        status = True
        while message_ids:
            message_chunk = message_ids[0:100] # collect upto 100 messages
            message_ids = message_ids[100:] # removed the collected messages

            try:
                await self.ctx.client.delete_messages(chat_id, message_chunk)
            except ChatAdminRequiredError as e:
                log_error(f"Can't delete messages {message_chunk}. Bot lacks required permissions in chat {chat_id}!")
                if custom_error_log:
                    asyncio.create_task(self.ctx.notification_manager.send_message_delete_failure(chat_id, message_chunk, custom_error_log, e))
                return False # no need to try for other chunks it wont work
            except Exception as e:
                log_error(f"Could not delete messages {message_chunk} in chat {chat_id}. Error: {e}")
                if custom_error_log:
                    asyncio.create_task(self.ctx.notification_manager.send_message_delete_failure(chat_id, message_chunk, custom_error_log, e))
                status = False
            await asyncio.sleep(1) # rate limits

        return status

    async def check_user_membership(self, user_id: int) -> bool:
        """Check if user is a member of required channels."""
        if not REQUIRED_CHANNELS_FORMATTED:
            return True
        try:
            # iterate through the list of tuples ("Name", "link")
            for element in REQUIRED_CHANNELS_FORMATTED:
                # only valid format lenths are allowed
                if len(element)==3:
                    name = element[0]
                    id = element[2]
                else:
                    continue

                try:
                    # Use the link for the check
                    await self.ctx.client(GetParticipantRequest(channel=id, participant=user_id))
                except UserNotParticipantError:
                    logger.warning(f"User {user_id} is not a participant in {name}.")
                    return False
                except Exception as e:
                    # cases where the link is invalid or the bot isn't an admin
                    logger.error(f"Could not check membership for user {user_id} in {name}: {e}")
                    return False
            return True
        except Exception as e:
            logger.error(f"General error in check_user_membership for user {user_id}: {e}")
            return False

    async def get_pack_input_from_event(self, event: events.NewMessage.Event) -> Any | None:
        """
        Parses an event to find sticker pack input from text, links, stickers, or custom emoji.
        Returns the pack input (e.g., stickerset, short_name) or None if not found.
        """
        pack_input = None
        
        if event.text:
            # if a text 
            done = False
            if event.message.entities: # if it has custom emojis
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        emoji_docs = await self.ctx.client(GetCustomEmojiDocumentsRequest(document_id=[entity.document_id]))
                        if not emoji_docs:
                            break
                        # first_emoji_doc = emoji_docs[0]
                        # print(first_emoji_doc.stringify())
                        for attribute in emoji_docs[0].attributes:
                            if isinstance(attribute, DocumentAttributeCustomEmoji):
                                pack_input = attribute.stickerset
                                done=True
                                break
                        break
            if not done: # assume its a link 
                pack_input = extract_pack_name_from_url(event.text)
                if not pack_input:
                    await event.reply(
                        "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> <b>Invalid input!</b>\n\n"
                        "Please send a valid Telegram sticker or emoji pack link, "
                        "or forward a sticker/emoji from the pack you want to convert.",
                        parse_mode="html"
                    )

        elif event.sticker:
            # if its a sticker
            for attr in event.sticker.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    pack_input = attr.stickerset
                    break
            
            if not pack_input:
                await event.reply(
                    "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> This sticker doesn't seem to belong to a pack I can access.\n\nPlease forward a sticker from a public sticker pack.",
                    parse_mode="html"
                )
        else:
            # if not a text or sticker
            await event.reply(
                    "<tg-emoji emoji-id='5465665476971471368'>❌</tg-emoji> <b>Invalid input!</b>\n\n"
                    "Please send a valid Telegram sticker or emoji pack link, "
                    "or forward a sticker/emoji from the pack you want to convert.",
                    parse_mode="html"
                )

        return pack_input


    async def get_user_from_event(self, event: events.NewMessage.Event, arg: Optional[str]) -> Optional[object]:
        """Helper to get user from command argument or reply."""
        entity = None
        if event.reply_to_msg_id and not arg:
            reply_msg = await event.get_reply_message()
            entity = await reply_msg.get_sender()
        elif arg:
            try:
                # Check if it's a numeric ID first
                if arg.isdigit():
                    entity = await self.ctx.client.get_entity(int(arg))
                else: # Assume it's a username
                    entity = await self.ctx.client.get_entity(arg)
            except Exception:
                await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Invalid user ID or username.", parse_mode="html")
                return None
        if entity is None:
            await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> Could not find the user.", parse_mode="html")
            return None
        elif not isinstance(entity, User) or entity.bot:
            await event.reply("<tg-emoji emoji-id='5980953710157632545'>❌</tg-emoji> This is not a valid user.", parse_mode="html")
            return None
        return entity

    async def update_customization_prompt(self, user_id: int, session: Session):
        """Edits the prompt message with the current customization state and buttons."""
        if not session or not session.active:
            return

        payload = session.payload
        original_event_info = payload['original_event_info']
        title = html.escape(payload['custom_title'] or payload['sticker_set_info']['title'])
        author = html.escape(payload['custom_author'] or self.ctx.bot_username)
        
        text = (
            f"<tg-emoji emoji-id='5947363097353130662'>✨</tg-emoji> <b>Premium Customization</b> <tg-emoji emoji-id='5947363097353130662'>✨</tg-emoji>\n\n"
            f"Here's the current setup for your pack:\n"
            f"<blockquote><tg-emoji emoji-id='5258215635996908355'>✏️</tg-emoji> <b>Title</b>: <code>{title}</code></blockquote>\n"
            f"<blockquote><tg-emoji emoji-id='5258011929993026890'>👤</tg-emoji> <b>Author</b>: <code>{author}</code></blockquote>\n"
            f"Ready to go, or want to make a change?"
        )
        
        sid = session.session_id
        buttons = [
            [Button.inline("Set Title", f"customize_title_{sid}", style="primary", icon=5258215635996908355), Button.inline("Set Author", f"customize_author_{sid}", style="primary", icon=5258011929993026890)],
            [Button.inline("Convert Now", f"customize_convert_{sid}", style="success", icon=5260416304224936047)],
            [Button.inline("Cancel", f"customize_cancel_{sid}", style="danger", icon=5260342697075416641)]
        ]
        
        try:
            if not payload['prompt_message_id']:
                bot_message = await self.ctx.client.send_message(
                    entity=original_event_info['chat_id'],
                    message=text,
                    buttons=buttons,
                    parse_mode='html',
                    reply_to=original_event_info['message_id']
                )
                payload['prompt_message_id'] = bot_message.id
                await session_manager.update(user_id, Flow.CUSTOMIZE, sid, payload_mutator=lambda p: p.update(payload))
            else:
                await self.ctx.client.edit_message(
                    original_event_info['chat_id'],
                    payload['prompt_message_id'],
                    text,
                    buttons=buttons,
                    parse_mode='html'
                )
        except Exception as e:
            logger.warning(f"Failed to send customization prompt to user {user_id}: {e}")


def estimate_wait_time(sticker_doc_info: list) -> float:
    """
    Calculates estimated wait time in seconds based on the type of each sticker/emoji.
    """
    total_seconds = 0.0

    # Time for processing each sticker/emoji
    for doc in sticker_doc_info:
        if doc == 'application/x-tgsticker':  # TGS file
            total_seconds += 2
        elif doc == 'video/webm':  # WebM
            total_seconds += 1
        elif doc == 'image/webp': # WebP
            total_seconds += 0.1
        else: # Others if any, we prbbly wont get any
            total_seconds += 1

    num_packs = math.ceil(len(sticker_doc_info)/30)
    total_seconds += DOWNLOAD_TIMEOUT * num_packs
    
    return total_seconds

# decorator for checking banned users
def check_banned(func):
    """Decorator to check if a user is banned before executing a command."""
    async def wrapper(self, event):
        if await db.is_banned(event.sender_id):
            logger.warning(f"Banned user {event.sender_id} tried to use the bot.")
            raise StopPropagation # Ignore
        return await func(self, event)
    return wrapper






