from typing import Optional
from src.db.pool import get_pool
from src.utils.time import utcnow


async def add_or_update_user(user_id: int, username: Optional[str], full_name: str):
    """Adds a new user or updates their details if they already exist."""
    pool = get_pool()
    safe_full_name = full_name[:129]
    now = utcnow()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # user update
            await conn.execute("""
                INSERT INTO users (user_id, username, first_seen)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username;
            """, user_id, username, now)
            # stats update
            await conn.execute("""
                INSERT INTO user_stats (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name;
            """, user_id, username, safe_full_name)

async def get_user_stats(user_id: int) -> dict:
    """Gets conversion stats for a specific user from the optimized table."""
    pool = get_pool()
    row = await pool.fetchrow("""
        SELECT total_requests, succeeded_requests, failed_requests, cancelled_requests
        FROM user_stats WHERE user_id = $1
    """, user_id)
    if row:
        return {
            "total": row["total_requests"] or 0,
            "succeeded": row["succeeded_requests"] or 0,
            "failed": row["failed_requests"] or 0,
            "cancelled": row["cancelled_requests"] or 0,
        }
    return {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0}

async def get_daily_usage(user_id: int) -> int:
    """
    Gets the number of requests a user has made today.
    Returns 0 if the last request was on a previous day.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT daily_requests, last_request_date FROM user_stats WHERE user_id = $1", user_id
    )
    if row and row['last_request_date']:
        today = utcnow().date()
        if row['last_request_date'] == today:
            return row['daily_requests'] or 0
    return 0

async def increment_daily_requests(user_id: int):
    """
    Increments the daily request counter for a user.
    If the last request was not today, it resets the counter to 1.
    """
    pool = get_pool()
    today = utcnow().date()
    await pool.execute("""
        UPDATE user_stats
        SET
            daily_requests = CASE
                WHEN last_request_date = $2 THEN daily_requests + 1
                ELSE 1
            END,
            last_request_date = $2
        WHERE user_id = $1
    """, user_id, today)

async def decrement_daily_requests(user_id: int):
    """
    Decrements the daily request counter for a user, e.g., after a cancellation.
    Only decrements if the last request was today and the count is positive.
    """
    pool = get_pool()
    today = utcnow().date()
    await pool.execute("""
        UPDATE user_stats
        SET daily_requests = daily_requests - 1
        WHERE user_id = $1 AND last_request_date = $2 AND daily_requests > 0
    """, user_id, today)

# fetching all user ids from database
async def get_all_user_ids() -> list[int]:
    """Retrieves all user IDs from the database for broadcasting."""
    pool = get_pool()
    rows = await pool.fetch("SELECT user_id FROM users")
    return [row["user_id"] for row in rows]
