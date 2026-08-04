import asyncpg
import logging
from typing import List, Optional
from datetime import timedelta
from src.db.pool import get_pool
from src.utils.time import utcnow
from src.core.config import CACHE_SCORE_TIME_WEIGHT, CACHE_SCORE_REQUEST_WEIGHT

logger = logging.getLogger(__name__)


async def add_or_update_sticker_set_stats(set_id: int, short_name: str, is_emoji: bool, pack_title: str, sticker_count: int, conversion_duration: float, is_system_process: bool) -> float:
    """
    Adds or updates a sticker pack's stats after a conversion.
    Calculates and returns the new cache score.
    """
    pool = get_pool()
    rounded_duration = round(conversion_duration, 2)
    now = utcnow()
    
    # we need to go twice for retry in case of short_name conflict
    for _ in range(2): 
        try:
            row = await pool.fetchrow(f"""
                INSERT INTO sticker_set_stats (
                    set_id, short_name, is_emoji, pack_title, sticker_count,
                    request_count, last_conversion_duration, cache_score, last_updated
                )
                VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8)
                ON CONFLICT (set_id) DO UPDATE SET
                    pack_title = EXCLUDED.pack_title,
                    short_name = EXCLUDED.short_name,
                    sticker_count = EXCLUDED.sticker_count,
                    request_count = CASE WHEN $9 THEN sticker_set_stats.request_count
                                         ELSE sticker_set_stats.request_count + 1 END,
                    last_conversion_duration = EXCLUDED.last_conversion_duration,
                    cache_score = ({CACHE_SCORE_TIME_WEIGHT} * EXCLUDED.last_conversion_duration) +
                                  ({CACHE_SCORE_REQUEST_WEIGHT} * (
                                      CASE WHEN $9 THEN sticker_set_stats.request_count
                                           ELSE sticker_set_stats.request_count + 1 END
                                  )),
                    last_updated = EXCLUDED.last_updated
                RETURNING cache_score
            """, set_id, short_name, is_emoji, pack_title, sticker_count,
                 rounded_duration,
                 (CACHE_SCORE_TIME_WEIGHT * rounded_duration) + (CACHE_SCORE_REQUEST_WEIGHT * 1),
                 now,
                 is_system_process)
            
            new_cache_score = float(row["cache_score"])
            logger.info(f"Updated stats for pack {short_name} (ID: {set_id}). New score: {new_cache_score:.2f}")
            return new_cache_score

        except asyncpg.UniqueViolationError as e:
            # check if its the short_name error
            if "sticker_set_stats_short_name_key" in str(e):
                logger.warning(f"Conflict detected for short_name '{short_name}' (New Set ID: {set_id}). "
                               f"Nullifying short_name for the old entry to resolve conflict.")
                async with pool.acquire() as conn, conn.transaction():
                    result = await conn.execute(
                        "UPDATE sticker_set_stats SET short_name = NULL WHERE short_name = $1", 
                        short_name
                    )
                    logger.info(f"Nullify result: {result}")
                # now we loop back and retry
                continue
            else:
                # some other error
                raise e

async def get_set_id_by_short_name(short_name: str) -> Optional[int]:

    """Finds a sticker set's ID by its short name in sticker_set_stats."""
    pool = get_pool()
    return await pool.fetchval("SELECT set_id FROM sticker_set_stats WHERE short_name = $1", short_name)

async def calculate_and_store_popular_packs():
    """
    Calculates the top 10 daily and top 50 all-time packs and stores them.
    This is intended to be run once daily.
    """
    pool = get_pool()
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    async with pool.acquire() as conn, conn.transaction():   
        # Start fresh
        await conn.execute("DELETE FROM popular_packs")

        # --- Calculate Daily Top 10 (from yesterday's data) ---
        
        # This query joins conversion logs with pack stats to get titles
        daily_packs = await conn.fetch("""
            SELECT
                sss.pack_title,
                cl.pack_url,
                COUNT(cl.set_id) as request_count
            FROM conversion_log cl
            JOIN sticker_set_stats sss ON cl.set_id = sss.set_id
            WHERE cl.request_time >= $1 AND cl.request_time < $2
            GROUP BY sss.pack_title, cl.pack_url, cl.set_id
            ORDER BY request_count DESC
            LIMIT 10;
        """, yesterday_start, today_start)

        daily_inserts = [(i, pack["pack_title"], pack["pack_url"], now) for i, pack in enumerate(daily_packs,1)]
        
        await conn.executemany(
            "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES ('daily', $1, $2, $3, $4)",
            daily_inserts
        )

        # --- Calculate All-Time Top 50 ---
        all_time_packs = await conn.fetch("""
            SELECT pack_title, short_name, is_emoji FROM sticker_set_stats WHERE short_name IS NOT NULL ORDER BY request_count DESC LIMIT 50
        """)
        
        all_time_inserts = []
        for i, pack in enumerate(all_time_packs, 1):
            pack_type_url = "addemoji" if pack['is_emoji'] else "addstickers"
            pack_url = f"https://t.me/{pack_type_url}/{pack['short_name']}"
            all_time_inserts.append((i, pack['pack_title'], pack_url, now))

        await conn.executemany(
            "INSERT INTO popular_packs (list_type, rank, pack_title, pack_url, last_updated) VALUES ('all_time', $1, $2, $3, $4)",
            all_time_inserts
        )

async def get_popular_packs(list_type: str) -> List[asyncpg.Record]:
    """Retrieves a pre-calculated list of popular packs."""
    pool = get_pool()
    return await pool.fetch(
            "SELECT pack_title, pack_url FROM popular_packs WHERE list_type = $1 ORDER BY rank ASC",
            list_type)

