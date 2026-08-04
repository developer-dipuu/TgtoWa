from typing import List
from src.db.pool import get_pool
from src.core.config import OWNER_ID
from src.utils.time import utcnow


async def get_all_admin_ids() -> List[int]:
    """Retrieves all admin and owner IDs from the database."""
    pool = get_pool()
    rows = await pool.fetch("SELECT user_id FROM admins")
    admin_ids = [row['user_id'] for row in rows]
    # Ensure owner is always included and the list is unique
    if OWNER_ID not in admin_ids:
        admin_ids.append(OWNER_ID)
    return admin_ids

async def add_admin(user_id: int, username: str, promoted_by: int) -> bool:
    """Promotes a user to admin."""
    pool = get_pool()
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        inserted = await conn.fetchval("""
            INSERT INTO admins (user_id, username, promoted_by, promotion_date)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING 1
        """, user_id, username, promoted_by, now
        )
        if inserted:
            await conn.execute("""
                INSERT INTO admin_history (admin_id, target_user_id, action, action_time)
                VALUES ($1, $2, 'promoted', $3)
            """, promoted_by, user_id, now
            )
            return True
    return False

async def remove_admin(user_id: int, demoted_by: int) -> bool:
    """Demotes an admin."""
    pool = get_pool()
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        deleted = await conn.fetchval(
            "DELETE FROM admins WHERE user_id = $1 RETURNING 1", user_id
        )
        if deleted:
            await conn.execute("""
                INSERT INTO admin_history (admin_id, target_user_id, action, action_time)
                VALUES ($1, $2, 'demoted', $3)
            """, demoted_by, user_id, now
            )
            return True
    return False
