
import asyncpg
from typing import Optional
from src.core.config import (DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT)

_pool: Optional[asyncpg.Pool] = None

async def init_pool():
    """Initializes the database connection pool."""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            host=DB_HOST,
            port=DB_PORT,
            min_size=5,
            max_size=20,
            command_timeout=10,
            statement_cache_size=1000,
            max_inactive_connection_lifetime=300
        )
        return _pool
    except Exception as e:
        raise RuntimeError(f"Could not create DB pool. Exception: {e}")

async def close_pool():
    """Closes the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

def get_pool() -> asyncpg.Pool:
    """Returns the database connection pool."""
    global _pool
    if _pool:
        return _pool
    raise RuntimeError("Could not get DB pool!")

