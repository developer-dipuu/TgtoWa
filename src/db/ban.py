from typing import Optional
from src.utils.time import utcnow
from src.db.pool import get_pool


async def is_banned(user_id: int) -> bool:
    """Checks if a user is in the banned table. This is the fast check."""
    pool = get_pool()
    return await pool.fetchrow(
        "SELECT 1 FROM banned_users WHERE user_id = $1", user_id
    ) is not None

async def ban_user(user_id: int, admin_id: int, reason: Optional[str], is_silent: bool) -> bool:
    """Adds a user to the banned_users table and logs it to history."""
    pool = get_pool()
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        # Add to currently banned table
        inserted = await conn.fetchval("""
            INSERT INTO banned_users (user_id, banned_by_admin_id, ban_date, is_silent, reason)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING 1
        """, user_id, admin_id, now, is_silent, reason)

        # Add to history log
        if inserted:
            await conn.execute("""
                INSERT INTO ban_history (target_user_id, admin_id, action, is_silent_ban, reason, action_time)
                VALUES ($1, $2, 'banned', $3, $4, $5)
            """, user_id, admin_id, is_silent, reason, now)
            return True
    return False

async def unban_user(user_id: int, admin_id: int, reason: Optional[str]) -> bool:
    """Removes a user from the banned_users table and logs it to history."""
    pool = get_pool()
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        deleted = await conn.fetchval(
            "DELETE FROM banned_users WHERE user_id = $1 RETURNING 1", user_id
        )
        if not deleted:
            return False
        await conn.execute("""
            INSERT INTO ban_history (target_user_id, admin_id, action, reason, action_time)
            VALUES ($1, $2, 'unbanned', $3, $4)
        """, user_id, admin_id, reason, now)
    return True
