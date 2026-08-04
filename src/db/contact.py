import asyncpg
import logging
from typing import List, Optional
from src.db.pool import get_pool
from src.utils.time import utcnow

logger = logging.getLogger(__name__)

async def log_contact_message(user_id: int, user_message_id: int, message_text: str) -> int:
    """Logs a new contact message from a user into the contact_messages table and returns contact_id"""
    pool = get_pool()
    row = await pool.fetchrow("""
        INSERT INTO contact_messages (user_id, user_message_id, user_message_text, action_time_sent)
        VALUES ($1, $2, $3, $4)
        RETURNING contact_id
    """, user_id, user_message_id, message_text, utcnow())
    contact_id = row['contact_id']
    logger.info(f"Logged new contact message from user {user_id}. contact_id: {contact_id}")
    return contact_id

async def log_admin_reply(contact_id: int, admin_id: int, admin_reply_message_id: int, reply_text: str):
    """Logs an admin's reply and updates the original message status."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Add the new reply to the replies table
        await conn.execute("""
            INSERT INTO admin_replies (contact_id, admin_id, admin_reply_message_id, admin_reply_text, action_time_replied)
            VALUES ($1, $2, $3, $4, $5)
        """, contact_id, admin_id, admin_reply_message_id, reply_text, utcnow())
        # Mark the original message as 'replied'
        await conn.execute("UPDATE contact_messages SET status = 'replied' WHERE contact_id = $1", contact_id)
    logger.info(f"Logged admin reply for contact_id {contact_id} by admin {admin_id}")

async def get_previous_replies(contact_id: int) -> List[asyncpg.Record]:
    """Checks for and returns any previous replies for a given contact_id."""
    pool = get_pool()
    return await pool.fetch("SELECT * FROM admin_replies WHERE contact_id = $1 ORDER BY action_time_replied ASC", contact_id)

async def get_contact_details(contact_id: int) -> Optional[dict]:
    """
    Fetches full details for a contact ticket, including the user's info,
    the original message, and all admin replies with admin info.
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        
        # Get the original message and join with user_stats to get the user's name
        contact_message = await pool.fetchrow("""
            SELECT cm.*, us.full_name as user_full_name
            FROM contact_messages cm
            LEFT JOIN user_stats us ON cm.user_id = us.user_id
            WHERE cm.contact_id = $1
        """, contact_id
        )

        if not contact_message:
            return None

        # Get all admin replies and join with user_stats to get admin names
        replies = await pool.fetch("""
            SELECT ar.*, us.full_name as admin_full_name
            FROM admin_replies ar
            LEFT JOIN user_stats us ON ar.admin_id = us.user_id
            WHERE ar.contact_id = $1
            ORDER BY ar.action_time_replied ASC
        """, contact_id)

    return {"user_message": contact_message, "admin_replies": replies}
