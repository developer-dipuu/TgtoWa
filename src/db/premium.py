import math
import json
from datetime import datetime, timedelta
from typing import Optional
import asyncpg
from src.db.pool import get_pool
from src.utils.time import utcnow


async def add_premium(user_id: int, username: str, admin_id: int, days: int, reason: str | None = None, 
                    payment_info: dict | None = None, conn: asyncpg.Connection | None = None) -> datetime:
    """Strictly only adds a user to premium if not already premium and logs the action. If you want to extend premium use manage_premium_duration.
    Expected payment_info format: {
        'user_id': int,
        'payment_method': str,
        'transaction_id': str,
        'amount': int,
        'currency': str,
        'status': str,
        'metadata': str | dict | None = None
    }
    Raises ValueError if the user is already premium.
    """
    pool = get_pool()
    now = utcnow()
    expiry_date = now + timedelta(days=days)
    
    opened_here = False
    if conn is None:
        conn = await pool.acquire()
        opened_here = True
    
    try:
        async with conn.transaction():
            inserted = await conn.fetchval(
                """
                INSERT INTO premium_users (user_id, username, added_by, start_date, expiry_date) 
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    added_by = EXCLUDED.added_by,
                    start_date = EXCLUDED.start_date,
                    expiry_date = EXCLUDED.expiry_date
                WHERE premium_users.expiry_date <= EXCLUDED.start_date
                RETURNING 1
                """,
                user_id, username, admin_id, now, expiry_date
            )
            if not inserted:
                raise ValueError(f"User {user_id} is already premium")
            # Log to history with all details
            payment_id = None
            if payment_info:
                metadata = payment_info.get('metadata', {})
                metadata_str = metadata if isinstance(metadata, str) else json.dumps(metadata) if metadata else "{}"
                
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
            await conn.execute(
                """INSERT INTO premium_history 
                (admin_id, target_user_id, payment_id, action, duration_change_days, previous_expiry_date, new_expiry_date, reason, action_time) 
                VALUES ($1, $2, $3, 'added', $4, NULL, $5, $6, $7)""",
                admin_id, user_id, payment_id, days, expiry_date, reason, now
            )
            return expiry_date
    finally:
        if opened_here:
            await pool.release(conn)

async def remove_premium(user_id: int, admin_id: int, payment_id: int | None = None, reason: str | None = None, conn: asyncpg.Connection | None = None) -> None:
    """Removes a user's premium subscription and logs the action."""
    pool = get_pool()
    now = utcnow()
    opened_here = False
    if conn is None:
        conn = await pool.acquire()
        opened_here = True
    try:
        async with conn.transaction():
            # Delete and get the current expiry date
            prev_expiry = await conn.fetchval(
                "DELETE FROM premium_users WHERE user_id = $1 and expiry_date > $2 RETURNING expiry_date", user_id, now
            )
            
            if not prev_expiry:
                raise ValueError(f"User {user_id} is not premium.")
            # Log the removal action

            duration_days = math.ceil((prev_expiry - now).total_seconds() / 86400)

            await conn.execute("""
                INSERT INTO premium_history (admin_id, target_user_id, payment_id, action, duration_change_days,
                                            previous_expiry_date, new_expiry_date, reason, action_time)
                VALUES ($1, $2, $3, 'removed', $4, $5, NULL, $6, $7)
            """, admin_id, user_id, payment_id, -duration_days, prev_expiry, reason, now
            )
    finally:
        if opened_here:
            await pool.release(conn)
    
async def get_premium_duration_left(user_id: int) -> Optional[timedelta]:
    """
    Calculates the remaining duration for a premium user's subscription.
    Returns a timedelta object if the subscription is active, otherwise None.
    """
    pool = get_pool()
    now = utcnow()
    expiry_date = await pool.fetchval(
        "SELECT expiry_date FROM premium_users WHERE user_id = $1 AND expiry_date > $2",
        user_id, now
    )
    if expiry_date:
        return expiry_date - now
    return None

async def manage_premium_duration(user_id: int, admin_id: int, action: str, days: int, reason: str | None = None,
                        payment_info: dict | None = None, conn: asyncpg.Connection | None = None) -> datetime:
    """Extends or deducts days from a premium subscription and logs the action. 'days' can be negative for deduction.
    If payment_info is provided: for extend it creates a payment entry and uses its payment_id,
    for deducts it uses the already given payment_id.
    Expected payment_info format for extends: {
        'user_id': int,
        'payment_method': str,
        'transaction_id': str,
        'amount': int,
        'currency': str,
        'status': str,
        'metadata': str | dict | None = None
    }
    Expected payment_info format for deducts: {'payment_id': int} and other params are ignored.
    If payment_info is None, it is assumed that the action is not related to a payment.
    Raises ValueError if user is not premium.
    It uses an existing connection if provided, otherwise it acquires a new one from the pool and releases it at the end.
    Returns the new expiry date.
    """
    pool = get_pool()
    now = utcnow()
    opened_here = False
    if conn is None:
        conn = await pool.acquire()
        opened_here = True
    try:
        async with conn.transaction():
            # update expiry and return old and new expiry
            expiry = await conn.fetchrow("""
                UPDATE premium_users
                SET expiry_date = expiry_date + $1::interval
                WHERE user_id = $2
                AND expiry_date > $3
                RETURNING expiry_date - $1::interval AS old_expiry,
                expiry_date AS new_expiry
            """, timedelta(days), user_id, now
            )

            if not expiry:
                raise ValueError(f"User {user_id} is not premium.") # User is not premium, cannot extend/deduct
            
            old_expiry = expiry['old_expiry']
            new_expiry = expiry['new_expiry']

            # get payment_id
            payment_id = None
            if payment_info:
                if action == 'extended':
                    metadata = payment_info.get('metadata', {})
                    metadata_str = metadata if isinstance(metadata, str) else json.dumps(metadata) if metadata else "{}"
                    # create new payment entry for extends
                    payment_id = await conn.fetchval(
                        """
                        Insert INTO payments (user_id, payment_method, transaction_id, amount, currency, status, duration_days, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING payment_id
                        """, 
                        payment_info['user_id'], payment_info['payment_method'], payment_info['transaction_id'], 
                        payment_info['amount'], payment_info['currency'], payment_info['status'], days, metadata_str
                    )

                    await conn.execute("""
                        INSERT INTO payment_history (payment_id, user_id, previous_status, new_status, actor_type, actor_id, old_metadata, new_metadata, reason, action_time)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    payment_id, payment_info['user_id'], "pending", payment_info['status'], "user",
                    payment_info['user_id'], None, metadata_str, reason, now
                    )

                else: # for deducts like manual or refunds
                    # payment id should be included in payment_info (if given)
                    payment_id = payment_info['payment_id']
                    
            # Log this action to premium_history
            await conn.execute(
                """INSERT INTO premium_history 
                (admin_id, target_user_id, payment_id, action, duration_change_days, previous_expiry_date, new_expiry_date, reason, action_time) 
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                admin_id, user_id, payment_id, action, days, old_expiry, new_expiry, reason, now
            )

        return new_expiry
    finally:
        if opened_here:
            await pool.release(conn)

async def deduct_premium_by_payment_id(payment_id: int, user_id: int, admin_id: int, reason: str | None = None) -> datetime | None:
    """
    Deducts premium from a user by payment id.
    Raises ValueError if payment is not found, not successful, or already deducted.
    Raises ValueError if user is not premium or user_id does not match.
    Returns the new expiry date if deducted, None if user is no longer premium after deduction.
    """
    pool = get_pool()
    now = utcnow()
    async with pool.acquire() as conn, conn.transaction():
        payment_info = await conn.fetchrow("""
        SELECT user_id, status, duration_days, is_deducted from payments
        WHERE payment_id = $1
        LIMIT 1
        """, payment_id)

        if not payment_info:
            raise ValueError(f"Payment not found for payment_id {payment_id}")
        if payment_info['is_deducted']:
            raise ValueError(f"Already deducted, premium for payment_id {payment_id} has already been deducted.")
        if payment_info['user_id'] != user_id:
            raise ValueError(f"Payment {payment_id} isn't associated with user {user_id}.")
        if payment_info['status'] in ('failed', 'pending'):
            raise ValueError(f"Payment isn't successful, cannot deduct premium for payment ID: {payment_id}.")
        
        prev_expiry = await conn.fetchval("""
            SELECT expiry_date FROM premium_users WHERE user_id = $1 AND expiry_date > $2
            """, user_id, now)

        if not prev_expiry:
            raise ValueError(f"User {user_id} is currently not premium, cannot deduct.")

        duration_days = payment_info['duration_days']
        # if days to be deducted > days left, then remove
        # (we ignore hours, minutes, seconds as if days to be deducted is even 1 more it'll cover those things)
        if duration_days > (prev_expiry - now).days:
            reason = (reason + " | " if reason else "") + "deduct > duration left"
            expiry = await remove_premium(user_id, admin_id, payment_id, reason=reason, conn=conn)
        # otherwise we have atleat sometime left so deduct
        else:
            days = -duration_days
            expiry = await manage_premium_duration(user_id, admin_id, 'deducted', days, reason=reason, payment_info={'payment_id': payment_id}, conn=conn)
        
        # mark as deducted
        await conn.execute("UPDATE payments SET is_deducted = true, updated_at = $2 WHERE payment_id = $1", payment_id, now)
        
    return expiry
            
async def remove_expired_premium_users() -> int:
    """
    Finds and removes premium users whose subscriptions have expired.
    Logs the removal to the premium_history table as an 'expired' action.
    Returns the number of users removed.
    """
    pool = get_pool()
    now = utcnow()
    system_admin_id = 0
    async with pool.acquire() as conn, conn.transaction():

        # Delete expired users and return user_id and expiry_date
        expired_users = await conn.fetch("""
            DELETE FROM premium_users
            WHERE expiry_date <= $1
            RETURNING user_id, expiry_date
        """, now)

        if not expired_users:
            return 0 # No one to remove

        # Log expired users
        history_logs = [
            (system_admin_id, u['user_id'], None, 'expired', None, u['expiry_date'], None, "Auto-removed: Subscription expired", now)
            for u in expired_users
        ]
        
        await conn.executemany("""
            INSERT INTO premium_history (admin_id, target_user_id, payment_id, action, duration_change_days,
                                        previous_expiry_date, new_expiry_date, reason, action_time)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, history_logs)
  
    return len(expired_users)
