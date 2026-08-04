import json
import asyncpg
import logging
from typing import Optional, List, Tuple
from src.utils.time import utcnow
from src.core.config import CACHE_CHANNEL_IDS, MAX_FILES_PER_CACHE_CHANNEL
from src.db.pool import get_pool
from src.db.pack_stats import add_or_update_sticker_set_stats

logger = logging.getLogger(__name__)

async def get_or_create_cache_channel() -> Optional[int]:
    """Finds a cache channel with space, or returns the next available one."""
    pool = get_pool()
    if not CACHE_CHANNEL_IDS:
        return None

    async with pool.acquire() as conn, conn.transaction():
        for channel_id in CACHE_CHANNEL_IDS:
            # Ensure row exists
            await conn.execute("""
                INSERT INTO cache_channels (channel_id, file_count)
                VALUES ($1, 0)
                ON CONFLICT (channel_id) DO NOTHING
            """, channel_id)
            # Lock row and read count
            row = await conn.fetchrow(
                "SELECT file_count FROM cache_channels WHERE channel_id = $1 FOR UPDATE",
                channel_id
            )

            if row and row["file_count"] < MAX_FILES_PER_CACHE_CHANNEL:
                return channel_id
        
    # If we get here, all configured channels are full
    logger.warning("⚠️ All available cache channels are full! ⚠️")
    return None

async def update_cache_channel_file_count(channel_id: int, file_delta: int):
    """Increments or decrements the file count for a cache channel."""
    pool = get_pool()
    await pool.execute(
            "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
            file_delta, channel_id
        )

async def is_pack_cached(set_id: int, current_title: str, current_sticker_count: int) -> Tuple[Optional[str], Optional[int], Optional[List[int]]]:
    """
    Checks if a pack is cached and if the cache is up-to-date.
    Returns: A tuple (status, channel_id, message_ids)
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():

        cache_result = await conn.fetchrow("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1", set_id,)

        if not cache_result:
            return 'miss', None, None

        channel_id = cache_result['channel_id']
        message_ids = json.loads(cache_result['message_ids'])

        stats_result = await conn.fetchrow("SELECT pack_title, sticker_count FROM sticker_set_stats WHERE set_id = $1", set_id,)

        if not stats_result or \
           stats_result['pack_title'] != current_title or \
           stats_result['sticker_count'] != current_sticker_count:
            logger.warning(f"Stale cache detected for pack {set_id}.")
            return 'stale', channel_id, message_ids
        
    logger.info(f"Cache hit for pack {set_id} in channel {channel_id}.")
    return 'hit', channel_id, message_ids

async def record_cache_hit(set_id: int, is_system_process: bool = False):
    """Updates a pack's stats to reflect a cache hit, increasing its score."""
    pool = get_pool()
    pack_info = await pool.fetchrow(
        "SELECT short_name, is_emoji, pack_title, sticker_count, last_conversion_duration FROM sticker_set_stats WHERE set_id = $1",
        set_id
    )
    if pack_info:
        await add_or_update_sticker_set_stats(
            set_id=set_id,
            short_name=pack_info['short_name'],
            is_emoji=pack_info['is_emoji'],
            pack_title=pack_info['pack_title'],
            sticker_count=pack_info['sticker_count'],
            conversion_duration=pack_info['last_conversion_duration'],
            is_system_process=is_system_process
        )

async def add_to_cache(set_id: int, cache_score: float, channel_id: int, message_ids: List[int]):
    """Adds a pack to the cache tracking table."""
    pool = get_pool()
    new_len = len(message_ids)
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        # See if this pack already exists
        old = await conn.fetchrow(
            "SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1 FOR UPDATE",
            set_id
        )
        old_channel_id = old["channel_id"] if old else None
        old_len = len(json.loads(old["message_ids"])) if old else 0

        await conn.execute("""
            INSERT INTO cached_packs (set_id, cache_score, cached_at, channel_id, message_ids)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (set_id) DO UPDATE SET
                cache_score = EXCLUDED.cache_score,
                cached_at   = EXCLUDED.cached_at,
                channel_id  = EXCLUDED.channel_id,
                message_ids = EXCLUDED.message_ids
        """, set_id, round(cache_score, 2), now, channel_id, json.dumps(message_ids))


        # Update the file count for the channel
        if old is None:
            # brand new pack
            await conn.execute(
                "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                new_len, channel_id
            )
        else:
            if old_channel_id == channel_id:
                # same channel, just adjust difference
                delta = new_len - old_len
                if delta:
                    await conn.execute(
                        "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                        delta, channel_id
                    )
            else:
                # moved pack from one channel to another
                await conn.execute(
                    "UPDATE cache_channels SET file_count = file_count - $1 WHERE channel_id = $2",
                    old_len, old_channel_id
                )
                await conn.execute(
                    "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
                    new_len, channel_id
                )
    logger.info(f"Added pack {set_id} to cache in channel {channel_id} with score {cache_score:.2f}")

async def remove_from_cache(set_id: int) -> Optional[Tuple[int, List[int]]]:
    """Removes a pack from the cache table and returns its location for message deletion."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "DELETE FROM cached_packs WHERE set_id = $1 RETURNING channel_id, message_ids",
            set_id
        )
        if not row:
            return None
        
        # Update the file count for the channel (decrement)
        channel_id, message_ids = row["channel_id"], json.loads(row["message_ids"])

        await conn.execute(
            "UPDATE cache_channels SET file_count = file_count - $1 WHERE channel_id = $2",
            len(message_ids), channel_id
        )

    logger.info(f"Removed pack {set_id} from cache DB.")
    return channel_id, message_ids

async def get_cached_pack_by_id(set_id: int) -> asyncpg.Record | None:
    """Gets the channel_id and message_ids for a single cached pack by its set_id. WARNING: make sure to json.loads() when acessing message_ids"""
    pool = get_pool()
    return await pool.fetchrow("SELECT channel_id, message_ids FROM cached_packs WHERE set_id = $1", set_id)

async def get_all_cached_pack_ids() -> List[int]:
    """Gets a list of set IDs of all currently cached packs."""
    pool = get_pool()
    results = await pool.fetch("SELECT set_id FROM cached_packs")
    return [result['set_id'] for result in results]

async def get_all_known_pack_short_names() -> List[str]:
    """Gets a list of short names of all currently known packs."""
    pool = get_pool()
    results = await pool.fetch("SELECT short_name FROM sticker_set_stats WHERE short_name IS NOT NULL")
    return [result['short_name'] for result in results]


async def get_top_packs_by_score(limit: int) -> List[str]:
    """Gets the top N sticker packs ordered by their cache score, returns a list of shortname strings"""
    pool = get_pool()
    rows = await pool.fetch(
            "SELECT short_name FROM sticker_set_stats WHERE short_name IS NOT NULL ORDER BY cache_score DESC LIMIT $1",
            limit)
    return [row['short_name'] for row in rows]


async def get_non_cached_packs(limit: Optional[int] = None) -> List[str]:
    """
    Gets a list of pack short_names from sticker_set_stats that are not in the cached_packs table.
    Results are ordered by cache_score descending to prioritize popular packs.
    """ 
    pool = get_pool()
    # find the entries in the sticker_set_stats that are not in cached_packs (on the basis of set_id)
    query = """
        SELECT sss.short_name
        FROM sticker_set_stats sss
        LEFT JOIN cached_packs cp ON sss.set_id = cp.set_id
        WHERE cp.set_id IS NULL AND sss.short_name IS NOT NULL
        ORDER BY sss.cache_score DESC
    """
    rows = []
    if limit and isinstance(limit, int) and limit > 0:
        query += " LIMIT $1"
        rows = await pool.fetch(query, limit)
    else:  
        rows = await pool.fetch(query)
    
    # Returns a simple list of short_name strings
    return [row['short_name'] for row in rows]

async def get_cache_info() -> Tuple[int, Optional[asyncpg.Record]]:
    """
    Gets the current number of items in the cache and the item with the lowest score.
    Returns a tuple: (current_cache_size, lowest_score_item_row).
    WARNING: make sure to json.loads() when acessing message_ids
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        count = await conn.fetchval("SELECT COUNT(*) FROM cached_packs")

        lowest_item = None
        if count > 0:
            lowest_item = await conn.fetchrow("SELECT * FROM cached_packs ORDER BY cache_score ASC LIMIT 1")
            
    return count, lowest_item


async def revert_cache_removal_and_log_junk(channel_id: int, message_ids: List[int], set_id: int, reason: str):
    """
    Corrects the file count after a failed deletion and logs the messages as junk.
    This is the reversal for the optimistic decrement in remove_from_cache.
    """
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Reincrement the file count because the files still exist
        await conn.execute(
            "UPDATE cache_channels SET file_count = file_count + $1 WHERE channel_id = $2",
            len(message_ids), channel_id
        )

        # Log the junk files for manual cleanup
        now = utcnow()
        records_to_insert = [
            (channel_id, msg_id, set_id, reason, now) for msg_id in message_ids
        ]
        await conn.executemany("""
            INSERT INTO junk_files (channel_id, message_id, set_id, reason, logged_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (channel_id, message_id) DO NOTHING
        """, records_to_insert)
    logger.info(f"Logged {len(message_ids)} junk files for set {set_id} in channel {channel_id}.")


async def get_all_junk_files_grouped() -> List[asyncpg.Record]:
    """Retrieves all junk files, grouped by their channel ID."""
    pool = get_pool()
    return await pool.fetch("""
        SELECT channel_id, array_agg(message_id ORDER BY message_id ASC) as message_ids
        FROM junk_files
        GROUP BY channel_id
        ORDER BY channel_id
    """)

async def clear_junk_file_entries() -> int:
    """
    Removes all entries from the junk_files table and returns the count of removed entries.
    This should be called after manual deletion of the files.
    """
    pool = get_pool()
    deleted_rows = await pool.fetch("DELETE FROM junk_files RETURNING 1")
    return len(deleted_rows)
