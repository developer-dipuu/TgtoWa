import asyncio
import html
import logging
from typing import List
from telethon import Button, events
from telethon.events import StopPropagation

from src import db
from src.bot.handlers.context import BotContext
from src.services.sessions.manager import Flow, Session, session_manager
from src.utils.formatters import get_user_display_name, get_message_content_for_db
from src.core.config import CONTACT_ADMIN_NOTIFICATION_HEADER, CONTACT_SUCCESS_MESSAGE, CONTACT_FAILURE_MESSAGE

logger = logging.getLogger(__name__)

class SessionHandler:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx

    async def get_active_input_sessions(self, user_id: int) -> List[tuple[Session, Flow]]:
        """Finds all active sessions for a user that are awaiting text input."""
        active_sessions_with_flow = []
        INPUT_AWAITING_STATES = {
            'awaiting_contact_message',
            'awaiting_custom_title',
            'awaiting_custom_author',
            'awaiting_addcache_input'
        }

        user_flows = await session_manager.get_all_user_sessions(user_id)
        for flow_val, sessions_list in user_flows.items():
            try:
                flow = Flow(flow_val) # Convert string from dict key back to Enum
                for session in sessions_list:
                    # We check if the session's state requires input
                    if session.state in INPUT_AWAITING_STATES:
                        active_sessions_with_flow.append((session, flow))
            except ValueError:
                logger.warning(f"Found session with unknown flow '{flow_val}' for user {user_id}")

        return active_sessions_with_flow
    
# session
    async def prompt_for_ambiguous_input(self, event: events.NewMessage.Event, sessions_with_flow: List[tuple[Session, Flow]]):
        """Notifies the user that their input is ambiguous and provides option to cancel."""

        all_ids_to_delete = {event.message.id}
        for session, _ in sessions_with_flow:
            old_prompt_ids = session.payload.get('ambiguity_prompt_ids', [])
            all_ids_to_delete.update(old_prompt_ids)

        if all_ids_to_delete:
            asyncio.create_task(self.ctx.helpers.delete_multiple_messages(
                event.chat_id,
                list(all_ids_to_delete),
                custom_error_log="Failed to delete old ambiguity prompts."
            ))

        text = (
            "<tg-emoji emoji-id='5472248119942979457'>🤔</tg-emoji> <b>Multiple Actions Pending</b>\n\n"
            "You have several actions waiting for your text input. "
            "To continue, please <b>scroll up and reply directly</b> to the correct prompt message or <b>cancel other actions</b> using the buttons below.\n\n"
            "Here are your pending actions:"
        )
        
        action_list = []
        buttons = []

        for session, flow in sessions_with_flow:
            payload = session.payload
            sid = session.session_id

            action_desc = "Unknown Action"
            button_text = ""
            if flow == Flow.CUSTOMIZE:
                pack_title = payload['sticker_set_info']['title']
                safe_pack_title = pack_title[:15] + "..." if len(pack_title) > 15 else pack_title
                if session.state == 'awaiting_custom_title':
                    action_desc = f"<tg-emoji emoji-id='5258215635996908355'>✏️</tg-emoji> Set Title for '{html.escape(safe_pack_title)}'"
                    button_text = f"Set Title for '{safe_pack_title}'"
                elif session.state == 'awaiting_custom_author':
                    action_desc = f"<tg-emoji emoji-id='5258011929993026890'>👤</tg-emoji> Set Author for '{html.escape(safe_pack_title)}'"
                    button_text = f"Set Author for '{safe_pack_title}'"
            elif flow == Flow.CONTACT:
                action_desc = "<tg-emoji emoji-id='5260535596941582167'>✉️</tg-emoji> Send Contact Message"
                button_text = "Send Contact Message"
            elif flow == Flow.ADDCACHE:
                action_desc = "<tg-emoji emoji-id='5258108352008823107'>➕️</tg-emoji> Add Cache Interactive"
                button_text = "Add Cache Interactive"

            action_list.append(f"{action_desc}")
            buttons.append([Button.inline(f"Cancel: {button_text}", f"cancel_session_{flow.value}_{sid}", style="danger", icon=5260342697075416641)])
        
        buttons.append([Button.inline("Cancel All Pending Actions", "cancel_all_input_sessions", style="danger", icon=5267123797600783095)])
    
        full_text = text + "\n" + "\n".join(action_list)
        prompt_msg = await event.respond(full_text, buttons=buttons, parse_mode="html")

        prompt_id = prompt_msg.id
        
        # Tag all the ambiguous sessions with the ID of the prompt we just sent
        # Why we are appending to the list instead of overriding as we have fired of delete tasks of old prompt ids?
        # because the messages are sent async so by the time next prompt arrive the old one might haven't been sent yet 
        # so appending make atleat the process_session_input (or when user sends ambiguous input slowly) delete all old prompts
        # otherwise it would have only deleted the last completed message but since async, multiple maybe sent if user inputs too fast
        for session, flow in sessions_with_flow:
            await session_manager.update(
                event.sender_id,
                flow,
                session.session_id,
                payload_mutator=lambda p, pid=prompt_id: p.setdefault('ambiguity_prompt_ids', []).append(pid)
            )

        raise StopPropagation
# session
    async def process_session_input(self, event: events.NewMessage.Event, session: Session, flow: Flow):
        """Routes a user's text message to the correct logic based on the session."""
        user_id = event.sender_id

        prompt_ids_to_delete = session.payload.get('ambiguity_prompt_ids')

        if prompt_ids_to_delete:
            asyncio.create_task(self.ctx.helpers.delete_multiple_messages(event.chat_id, prompt_ids_to_delete, "Failed to delete ambagious prompt."))
            
            # Clean the ambiguity_prompt_ids from all active sessions for this user
            all_active = await self.get_active_input_sessions(user_id)
            for active_session, active_flow in all_active:
                if 'ambiguity_prompt_ids' in active_session.payload:
                    await session_manager.update(
                        user_id,
                        active_flow,
                        active_session.session_id,
                        payload_mutator=lambda p: p.pop('ambiguity_prompt_ids', None)
                    )

        # --- CONTACT MESSAGE ---
        if flow == Flow.CONTACT and session.state == 'awaiting_contact_message':
            await session_manager.expire(user_id, Flow.CONTACT, session.session_id) # Expire after use

            message_content = get_message_content_for_db(event.message)
            contact_id = await db.log_contact_message(user_id, event.message.id, message_content)
            admin_ids = await db.get_all_admin_ids()

            user = await event.get_sender()
            user_display_name = get_user_display_name(user)
            role = "<tg-emoji emoji-id='5258165702707125574'>⭐</tg-emoji> Premium User" if await db.is_premium(user.id) else "<tg-emoji emoji-id='5316727448644103237'>👤</tg-emoji> Regular User"
            stats = await db.get_user_stats(user.id)

            header_message = CONTACT_ADMIN_NOTIFICATION_HEADER.format(
                contact_id=contact_id, 
                user_display_name=html.escape(user_display_name),
                user_id=user.id, 
                role=role, 
                succeeded=stats['succeeded'],
                failed=stats['failed'], 
                cancelled=stats['cancelled'], 
                total=stats['total']
            )

            single_success = False
            for admin_id in admin_ids:
                try:
                    await self.ctx.client.send_message(admin_id, header_message, parse_mode='html')
                    await self.ctx.client.forward_messages(admin_id, event.message)
                    single_success = True
                    logger.debug(f"Forwarded the {user.id} user's contact message to the admin {admin_id}")
                except Exception as e:
                    logger.warning(f"Failed to forward contact message to admin {admin_id}: {e}")
            
            if single_success:
                await event.reply(CONTACT_SUCCESS_MESSAGE, parse_mode='html')
            else:
                await event.reply(CONTACT_FAILURE_MESSAGE, parse_mode='html')
            raise StopPropagation

        # --- CUSTOMIZATION INPUT ---
        elif flow == Flow.CUSTOMIZE and session.state in ('awaiting_custom_title', 'awaiting_custom_author'):
            payload = session.payload

            if not event.text or not event.text.strip():
                await event.delete()
                msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Only valid <b>text messages</b> are allowed. Please try again.", parse_mode='html')
                await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id,
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                return

            user_input = event.text.strip()

            if session.state == 'awaiting_custom_title':
                if len(user_input) > 50:
                    await event.delete()
                    msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Title too long (max 50 chars). Please try again.", parse_mode='html')
                    await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, 
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                    return
                payload['custom_title'] = user_input

            elif session.state == 'awaiting_custom_author':
                if len(user_input) > 30:
                    await event.delete()
                    msg = await event.respond("<tg-emoji emoji-id='5915991028430542030'>⚠️</tg-emoji> Author name too long (max 30 chars). Please try again.", parse_mode='html')
                    await session_manager.update(user_id, Flow.CUSTOMIZE, session.session_id, 
                                                 payload_mutator=lambda p: p.setdefault('failed_inputs', []).append(msg.id))
                    return
                payload['custom_author'] = user_input

            asyncio.create_task(self.ctx.helpers.react(event, emoji= "🆒", big=True))
            
            messages_to_delete = payload.get("failed_inputs", [])
            payload['failed_inputs'] = []

            session.state = 'awaiting_customization_choice' # Go back to the main menu
            await session_manager.update(
                user_id, Flow.CUSTOMIZE, session.session_id,
                state='awaiting_customization_choice',
                payload_mutator=lambda p: p.update(payload),
                ttl_seconds=3600
            )
            await self.ctx.helpers.update_customization_prompt(user_id, session)
            asyncio.create_task(self.ctx.helpers.delete_multiple_messages(event.chat_id, messages_to_delete, "Failed to delete invalid customization input messages."))
            raise StopPropagation

        # --- ADD CACHE INPUT ---
        elif flow == Flow.ADDCACHE and session.state == 'awaiting_addcache_input':
            await self.ctx.core.execute_interactive_addcache(event)
            raise StopPropagation


