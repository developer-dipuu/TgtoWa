from datetime import datetime
from src.db.pool import get_pool
from src.utils.time import utcnow


async def log_conversion_request(user_id: int, set_id: int, pack_url: str, is_emoji: bool) -> int:
    """Logs the start of a conversion request and returns the log ID."""
    pool = get_pool()
    row = await pool.fetchrow("""
        INSERT INTO conversion_log (user_id, set_id, pack_url, is_emoji, status, request_time)
        VALUES ($1, $2, $3, $4, 'processing', $5)
        RETURNING log_id
    """, user_id, set_id, pack_url, is_emoji, utcnow())
    return row["log_id"]

async def update_conversion_log(log_id: int, status: str, completion_time: datetime, duration: float):
    """Updates a conversion log entry and the user's stats."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # First update the detailed log
        row = await conn.fetchrow("UPDATE conversion_log SET status = $1, completion_time = $2, duration_seconds = $3 WHERE log_id = $4 RETURNING user_id", 
            status, completion_time.replace(microsecond=0), round(duration, 2), log_id
        )
        if not row:
            return
        user_id = row["user_id"]

        # Update the stats table, increment the correct counter
        if status.startswith("completed"):
            await conn.execute("UPDATE user_stats SET succeeded_requests = succeeded_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        elif status.startswith("failed"):
            await conn.execute("UPDATE user_stats SET failed_requests = failed_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        elif status.startswith("cancelled"):
            await conn.execute("UPDATE user_stats SET cancelled_requests = cancelled_requests + 1, total_requests = total_requests + 1 WHERE user_id = $1", user_id)
        else:
            await conn.execute("UPDATE user_stats SET total_requests = total_requests + 1 WHERE user_id = $1", user_id)
