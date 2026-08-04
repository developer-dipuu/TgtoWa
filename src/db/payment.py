import asyncpg
import json
from src.utils.time import utcnow
from src.db.pool import get_pool


async def get_payment_by_transaction_id(transaction_id: str) -> asyncpg.Record | None:
    pool = get_pool()
    return await pool.fetchrow("""
    SELECT * FROM payments WHERE transaction_id = $1
    """, transaction_id)
    
async def update_payment_status(payment_id: int, status: str, actor_type: str, actor_id: int, reason: str | None = None, 
                                metadata: str |dict | None = None, conn: asyncpg.Connection | None = None) -> None:
    """
    Updates the status of a payment and logs the change to the payment_history table.
    Raises ValueError if payment not found.
    """
    pool = get_pool()
    metadata_str = metadata if isinstance(metadata, str) else json.dumps(metadata) if metadata else "{}"
    now = utcnow()
    update_query = """
    WITH old_data AS (
        SELECT status AS old_status, 
            metadata AS old_metadata, 
            user_id
        FROM payments
        WHERE payment_id = $4
        FOR UPDATE
    ),
    updated_payment AS (
        UPDATE payments
        SET status = $1,
            metadata = COALESCE($2, metadata),
            updated_at = $3
        WHERE payment_id = $4
    )
    SELECT old_status, old_metadata, user_id
    FROM old_data
    """
    history_query = """
        INSERT INTO payment_history (payment_id, user_id, previous_status, new_status, actor_type, actor_id, old_metadata, new_metadata, reason, action_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """
    if conn is None:
        async with pool.acquire() as conn, conn.transaction():
            payment_info = await conn.fetchrow(update_query, status, metadata_str, now, payment_id)

            if payment_info is None:
                raise ValueError(f"Payment not found for payment_id {payment_id}")

            await conn.execute(history_query,
                payment_id, payment_info['user_id'], payment_info['old_status'], status, 
                actor_type, actor_id, payment_info['old_metadata'], metadata_str, reason, now
            )
    else:
        async with conn.transaction():
            payment_info = await conn.fetchrow(update_query, status, metadata_str, now, payment_id)
        
            if payment_info is None:
                raise ValueError(f"Payment not found for payment_id {payment_id}")

            await conn.execute(history_query,
                payment_id, payment_info['user_id'], payment_info['old_status'], status, 
                actor_type, actor_id, payment_info['old_metadata'], metadata_str, reason, now
            )
 
async def record_payment(payment_info: dict, days: int, reason: str):
    pool = get_pool()
    now = utcnow()
    metadata = payment_info.get('metadata', {})
    metadata_str = metadata if isinstance(metadata, str) else json.dumps(metadata) if metadata else "{}"
    async with pool.acquire() as conn, conn.transaction():
        payment_id = await conn.fetchval(
            """
            INSERT INTO payments (user_id, payment_method, transaction_id, amount, currency, status, duration_days, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING payment_id
            """, 
            payment_info['user_id'], payment_info['payment_method'], payment_info['transaction_id'], 
            payment_info['amount'], payment_info['currency'], payment_info['status'], days, metadata_str
        )
        
        # add to payment history
        await conn.execute("""
            INSERT INTO payment_history (payment_id, user_id, previous_status, new_status, actor_type, actor_id, old_metadata, new_metadata, reason, action_time)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        payment_id, payment_info['user_id'], "pending", payment_info['status'], "user",
        payment_info['user_id'], None, metadata_str, reason, now
        )
