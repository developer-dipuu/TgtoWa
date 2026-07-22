import logging
from typing import Any
from telethon import TelegramClient, events
from telethon.events import StopPropagation
from telethon.tl.types import (
    InputMediaInvoice,
    Invoice,
    LabeledPrice,
    DataJSON,
    UpdateBotPrecheckoutQuery,
    UpdateNewMessage,
    MessageActionPaymentSentMe
)
from telethon.tl.functions.messages import SetBotPrecheckoutResultsRequest
from telethon.tl.functions.payments import RefundStarsChargeRequest
import database as db
from config import OWNER_ID
from notification_manager import NotificationManager
from utils import get_user_display_name

logger = logging.getLogger(__name__)
SYSTEM_USER_ID = 0

class PaymentManager:
    """
    Payment system specifically designed for Telegram Stars (XTR).
    """
    def __init__(self, client: TelegramClient, notification_manager: NotificationManager):
        self.client = client
        self.notification_manager = notification_manager

    def register_handlers(self):
        """Registers the events needed to process Telegram Star payments."""
        self.client.add_event_handler(
            self.handle_precheckout, 
            events.Raw(types=UpdateBotPrecheckoutQuery)
        )
        
        # catching the Raw UpdateNewMessage directly
        self.client.add_event_handler(
            self.handle_payment_sent, 
            events.Raw(types=UpdateNewMessage)
        )


    def create_stars_invoice(self, title: str, description: str, payload: str, amount: int) -> InputMediaInvoice:
        return InputMediaInvoice(
            title=title,
            description=description,
            invoice=Invoice(
                currency='XTR', 
                prices=[LabeledPrice(label='Premium Upgrade', amount=amount)],
                test=False, 
                name_requested=False,
                phone_requested=False,
                email_requested=False,
                shipping_address_requested=False,
                flexible=False,
                phone_to_provider=False,
                email_to_provider=False
            ),
            payload=payload.encode('utf-8'),
            provider='', 
            provider_data=DataJSON('{}'),
            start_param='buy_premium'
        )

    async def handle_precheckout(self, event: UpdateBotPrecheckoutQuery):
        try:
            payload_str = event.payload.decode('utf-8')
            logger.info(f"Received Stars pre-checkout query {event.query_id} for {payload_str} from user {event.user_id}")
            
            await self.client(SetBotPrecheckoutResultsRequest(
                query_id=event.query_id,
                success=True,
                error=None
            ))
        except Exception as e:
            logger.error(f"Failed to handle pre-checkout query: {e}")
            try:
                await self.client(SetBotPrecheckoutResultsRequest(
                    query_id=event.query_id,
                    success=False,
                    error="Internal server error while preparing the invoice."
                ))
            except Exception:
                pass

    async def handle_payment_sent(self, event: UpdateNewMessage):

        """Processes the payment and grants premium privileges to the user."""
        
        # extract message and action from raw event
        message = getattr(event, 'message', None)
        if not message:
            return
            
        action = getattr(message, 'action', None)
        if not isinstance(action, MessageActionPaymentSentMe):
            return
            
        # check it's stars
        if getattr(action, 'currency', '') != 'XTR':
            return
            
        payload = action.payload.decode('utf-8')
        
        # extract user id from the raw peer object
        try:
            user_id = message.peer_id.user_id
        except AttributeError:
            user_id = getattr(message.from_id, 'user_id', None)
            
        if not user_id:
            logger.error("Stars payment received, but could not determine user_id from the raw event!")
            return

        charge_id = action.charge.id
        amount = action.total_amount
        metadata=action.to_json()
        
        # check duplicate payment
        existing = await db.get_payment_by_transaction_id(charge_id)
        if existing:
            logger.warning(f"Duplicate payment event for charge ID: {charge_id}, ignoring")
            return

        
        logger.info(f"Stars payment received from user {user_id}: {amount} XTR for payload {payload}")
        logger.info(f"CHARGE ID: {charge_id}")
        
        payment_info = {
            'user_id': user_id,
            'payment_method': 'stars',
            'transaction_id': charge_id,
            'amount': amount,
            'currency': 'XTR',
            'status': 'success',
            'metadata': metadata
        }
        
        try:
            if not payload.startswith("premium_"):
                raise ValueError(f"Unknown payload: {payload}")        
                       
            duration_days = int(payload.split("_")[1])
            
            # We have to manually fetch the user entity because this is a Raw event
            user = await self.client.get_entity(user_id)
            username = getattr(user, 'username', "") or ""
            
            # Update database
            is_premium = await db.is_premium(user_id)

            if is_premium:
                # some more stuffs do here
                expiry = await db.manage_premium_duration(
                    user_id=user_id, 
                    admin_id=SYSTEM_USER_ID, 
                    action='extended',
                    days=duration_days, 
                    reason="premium purchase",
                    payment_info=payment_info

                )
                logger.info(f"Premium extended for user {user_id}: {duration_days} days")
            else:
                expiry = await db.add_premium(
                    user_id=user_id, 
                    username=username, 
                    admin_id=SYSTEM_USER_ID,
                    days=duration_days, 
                    reason="premium purchase",
                    payment_info=payment_info
                )
                logger.info(f"Premium added for user {user_id}: {duration_days} days")
            
            # Send confirmation to user
            await self.client.send_message(
                user_id,
                f"<tg-emoji emoji-id='5967522716062847679'>⭐</tg-emoji> <b>Payment Successful!</b>\n\n"
                f"Thank you for your purchase! We've successfully added <b>{duration_days} days</b> of Premium access to your account.",
                parse_mode='html'
            )
            # send payment success notification to owner
            try:
                await self.notification_manager.send_premium_purchase(user_id, get_user_display_name(user), payment_info, duration_days)
            except Exception as e: 
                logger.error(f"Error sending premium purchase notification to owner: {e}")
        except Exception as e:
            logger.error(f"Error granting premium to {user_id} after Stars payment: {e}")

            # record payment to database
            record_success = False
            try:
                await db.record_payment(payment_info, 0, "premium purchase (failed)")
                record_success = True
            except Exception as e1:
                logger.error(f"Error recording payment with charge ID: {charge_id} for user {user_id}: {e1}")
            
            # refund the payment 
            refund_success = False
            try:
                await self.refund(user_id, SYSTEM_USER_ID, charge_id, "system", SYSTEM_USER_ID, "refunded due to premium purchase failed", deduct=False, no_db=(not record_success))
                refund_success = True
                try:
                    await self.client.send_message(user_id, "<tg-emoji emoji-id='5420323339723881652'>⚠️</tg-emoji> Payment received, but an error occurred while upgrading your account.\n<b>We have refunded the payment.</b>\nPlease contact support via <b>/contact</b>.", parse_mode='html')
                except Exception as e2_1:
                    logger.error(f"Error sending refund message to user {user_id}: {e2_1}")
            except Exception as e2:
                logger.error(f"Error refunding payment with charge ID: {charge_id} for user {user_id}: {e2}")
                try:
                    await self.client.send_message(user_id, "<tg-emoji emoji-id='5420323339723881652'>⚠️</tg-emoji> Payment received, but an error occurred while upgrading your account. Please contact support via <b>/contact</b>.", parse_mode='html')
                except Exception as e2_1:
                    logger.error(f"Error sending premium grant failed notification to user {user_id}: {e2_1}")

            # send premium grant failed notification to owner
            try:
                await self.notification_manager.send_premium_grant_failed(user_id, payment_info, record_success, refund_success, str(e))
            except Exception as e3: 
                logger.error(f"Error sending premium grant failed notification to owner: {e3}")


    async def refund(self, user_id: int, admin_id: int, charge_id: str, actor_type: str, actor_id: int,
                        reason: str, deduct: bool = False, no_db: bool = False) -> dict[str, Any]:
        """
        Refunds stars for a user with the given charge ID.

        :param user_id: The ID of the user to refund.
        :param admin_id: The ID of the admin performing the refund.
        :param charge_id: The charge ID of the payment to refund.
        :param deduct: Whether to deduct premium access from the user.
        :param no_db: Whether to skip database operations (for emergency refunds).
        :return: A dictionary containing the refund result.

        Return structure:
            {
                "refund_success": bool,
                "refund_error": Optional[str],
                "status_updated": bool,
                "status_update_error": Optional[str],
                "deduction_info": Optional[dict],
                "payment": Optional[asyncpg.Record]
            }

        Raises:
            ValueError: If the payment is not found, not successful, or the user ID does not match.
        """
        # if something happens wrong with database and we need to refund manually
        if no_db:
            try:
                # We call the raw MTProto function to refund the stars
                await self.client(RefundStarsChargeRequest(
                    user_id=await self.client.get_input_entity(user_id),
                    charge_id=charge_id
                ))
                logger.info(f"Refunded stars for user {user_id} with charge ID {charge_id}")
                return {'refund_success': True, 'refund_error': None, 'status_updated': False, 'status_update_error': None, 'deduction_info': None, 'payment': None}
            except Exception as e:
                logger.error(f"Failed to refund stars for user {user_id} with charge ID {charge_id}: {e}")
                return {'refund_success': False, 'refund_error': str(e), 'status_updated': False, 'status_update_error': None, 'deduction_info': None, 'payment': None}
        
        # fetch payment details from the database
        payment = await db.get_payment_by_transaction_id(charge_id)

        if not payment:
            logger.error(f"Payment not found for user {user_id} with charge ID {charge_id}")
            raise ValueError("payment_not_found")
        if payment['status'] != 'success':
            logger.error(f"Payment status is not success for user {user_id} with charge ID {charge_id}")
            raise ValueError("payment_not_success")
        if int(payment['user_id']) != user_id:
            logger.error(f"User ID mismatch for user {user_id} with charge ID {charge_id}")
            raise ValueError("user_id_mismatch")
        
        payment_id = payment['payment_id']
        amount = payment['amount']


        refund_success = False
        refund_error = None
        status_update = False
        status_update_error = None
        deduction_info = None
        # refund
        try:
            # call the raw mtproto function to refund the stars
            captured_event = await self.client(RefundStarsChargeRequest(
                user_id=await self.client.get_input_entity(user_id),
                charge_id=charge_id
            ))

            refund_success = True
            # mark payment as refunded
            try:
                action = None
                
                # iterate through the returned updates list to find the message action
                if hasattr(captured_event, 'updates'):
                    for update in captured_event.updates:
                        msg = getattr(update, 'message', None)
                        if msg and getattr(msg, 'action', None):
                            action = msg.action
                            break

                if action:
                    metadata = action.to_json()
                    await db.update_payment_status(payment_id, "refunded", actor_type, actor_id, reason, metadata)
                else:
                    # in case action object wasnt found in updates
                    logger.warning(f"No action object found in refund updates for charge {charge_id}")
                    await db.update_payment_status(payment_id, "refunded", actor_type, actor_id, reason, captured_event.to_json())
                
                status_update = True
            except Exception as e:
                logger.error(f"Failed to mark payment as refunded for user {user_id} with charge ID {charge_id}: {e}")
                status_update_error = str(e)
            
            logger.info(f"Refunded {amount} stars for user {user_id} with charge ID {charge_id}")
        
        except Exception as e:
            logger.error(f"Refund failed for user {user_id} with charge ID {charge_id}: {e}")
            refund_error = str(e)
        
        # deduct premium if requested and refund was successful
        if refund_success and deduct:
            deduction_info = {"action": "no action taken", "new_expiry_date": None, "error": None}
            try:
                new_expiry_date = await db.deduct_premium_by_payment_id(payment_id, user_id, admin_id, "refunded")
                deduction_info = {"action": "deducted" if new_expiry_date else "removed", "new_expiry_date": new_expiry_date, "error": None}
            except Exception as e:
                logger.error(f"Failed to deduct premium for user {user_id} with charge ID {charge_id}: {e}")
                deduction_info = {"action": "failed", "new_expiry_date": None, "error": str(e)}
        
        return {
            'refund_success': refund_success,
            'refund_error': refund_error,
            'status_updated': status_update,
            'status_update_error': status_update_error,
            'deduction_info': deduction_info,
            'payment': payment
        }
