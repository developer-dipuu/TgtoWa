from src.core.config import OWNER_ID
from src.db.pool import get_pool
from src.utils.time import utcnow


def is_owner(user_id: int) -> bool:
    """Checks if a user is the owner."""
    return user_id == OWNER_ID

async def is_user(user_id: int) -> bool:
    """Checks if a user has started the bot."""
    pool = get_pool()
    return await pool.fetchval(
        "SELECT 1 FROM users where user_id = $1", user_id
    ) is not None

async def is_admin(user_id: int) -> bool:
    """Checks if a user is an admin or the owner."""
    pool = get_pool()
    if is_owner(user_id):
        return True
    return await pool.fetchval("SELECT 1 FROM admins WHERE user_id = $1", user_id) is not None

async def is_premium(user_id: int) -> bool:
    """Checks if a user has an active premium subscription."""
    pool = get_pool()
    return await pool.fetchval(
        "SELECT 1 FROM premium_users WHERE user_id = $1 AND expiry_date > $2",
        user_id, utcnow()
    ) is not None
