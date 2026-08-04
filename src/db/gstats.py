from src.utils.time import utcnow
from src.db.pool import get_pool

# for the owner's /gstats command
async def get_gstats() -> dict:
    """Gathers all global statistics for the /gstats command."""
    pool = get_pool()
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM users) AS total_users,
            (SELECT COUNT(*) FROM admins) AS total_admins,
            (SELECT COUNT(*) FROM banned_users) AS total_banned,
            (SELECT COUNT(*) FROM premium_users WHERE expiry_date > NOW()) AS active_premium,
            (SELECT COALESCE(SUM(succeeded_requests), 0) FROM user_stats) AS total_succeeded,
            (SELECT COALESCE(SUM(failed_requests), 0) FROM user_stats) AS total_failed,
            (SELECT COALESCE(SUM(cancelled_requests), 0) FROM user_stats) AS total_cancelled,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'completed%%') AS today_succeeded,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'failed%%') AS today_failed,
            (SELECT COUNT(*) FROM conversion_log WHERE request_time >= $1 AND status LIKE 'cancelled%%') AS today_cancelled

    """, today_start)
    return dict(row)

async def get_gstats_premium_list() -> list:
    """Gets a list of all active premium users for /gstats."""
    pool = get_pool()
    now = utcnow()
    list = await pool.fetch(
        "SELECT user_id, username, expiry_date FROM premium_users WHERE expiry_date > $1 ORDER BY expiry_date ASC",
        now
    )
    return list

async def get_gstats_top_users(limit: int = 50) -> list:
    """Gets a list of top users by total requests."""
    pool = get_pool()
    list = await pool.fetch(
        "SELECT user_id, full_name, total_requests FROM user_stats ORDER BY total_requests DESC LIMIT $1",
        limit
    )
    return list

async def get_gstats_admins_list() -> list:
    """Gets a list of all admins."""
    pool = get_pool()
    list = await pool.fetch("SELECT user_id, username FROM admins")
    return list

async def get_gstats_banned_list() -> list:
    """Gets a list of all banned users."""
    pool = get_pool()
    list = await pool.fetch("SELECT user_id, reason, ban_date FROM banned_users ORDER BY ban_date DESC")
    return list
