import json
import logging
from typing import Optional, List
from src.db.pool import get_pool
from src.utils.time import utcnow

logger = logging.getLogger(__name__)


async def log_broadcast(admin_id: int, message_content: str, flags: str,
                  total_users: int, success_count: int, fail_count: int,
                  is_forward: bool = False, forwarded_from_id: Optional[int] = None,
                  forwarded_message_id: Optional[int] = None):
    """Logs a broadcast event to the database."""
    pool = get_pool()
    await pool.execute("""
        INSERT INTO broadcast_log (
            admin_id, action_time, message_content, flags, total_users,
            success_count, fail_count, is_forward, forwarded_from_id, forwarded_message_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """, admin_id, utcnow(), message_content, flags, total_users,
       success_count, fail_count, is_forward, forwarded_from_id, forwarded_message_id)
    
    logger.info(f"Logged broadcast from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")

async def log_send(admin_id: int, message_content: str, flags: str,
             target_users_list: List[int], success_count: int, fail_count: int,
             is_forward: bool = False, forwarded_from_id: Optional[int] = None,
             forwarded_message_id: Optional[int] = None):
    """Logs a /send event to the database."""
    pool = get_pool()
    await pool.execute("""
        INSERT INTO send_log (
            admin_id, action_time, message_content, flags, target_users_list,
            total_users, success_count, fail_count, is_forward, forwarded_from_id,
            forwarded_message_id
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11)
    """, admin_id, utcnow(), message_content, flags, json.dumps(target_users_list),
       len(target_users_list), success_count, fail_count, is_forward, forwarded_from_id,
       forwarded_message_id)

    logger.info(f"Logged /send from admin {admin_id}. Success: {success_count}, Fail: {fail_count}")
