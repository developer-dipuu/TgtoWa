"""
Persistant session manager for the bot, backed by PostgreSQL.
"""
import secrets
import json
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import asyncio
import logging
import asyncpg

from src.db import get_pool

logger = logging.getLogger(__name__)

class Flow(str, Enum):
    CONTACT = "c"
    CUSTOMIZE = "cz"
    ADDCACHE = "ac"

class Session:
    """A pure data class to represent a session retrieved from the database."""
    def __init__(self, row: asyncpg.Record):
        self.user_id: int = row['user_id']
        self.flow: Flow = Flow(row['flow'])
        self.session_id: str = row['session_id']
        self.state: Optional[str] = row['state']
        self.payload: Dict[str, Any] = json.loads(row['payload']) or {}
        self.created_at: datetime = row['created_at']
        self.expires_at: Optional[datetime] = row['expires_at']

    @property
    def active(self) -> bool:
        """A session is active if it has not expired."""
        if self.expires_at is None:
            return True
        return self.expires_at > datetime.now(timezone.utc)

class SessionManager:
    def __init__(self):
        self._msg_index: Dict[tuple, tuple] = {}
        self._msg_index_lock = asyncio.Lock()
        self.pool = get_pool()

    async def rebuild_msg_index(self):
        """
        Populates the in-memory message index from the database on startup.
        This is key to making sessions persistent across restarts.
        """
        logger.info("Rebuilding in-memory session message index from database...")
        rebuilt_count = 0
        async with self._msg_index_lock:
            self._msg_index.clear()
            rows = await self.pool.fetch(
                """
                SELECT user_id, flow, session_id, payload->'prompt_message_id' as pmid, payload->'original_event_info'->'chat_id' as cid
                FROM sessions
                WHERE expires_at > NOW() AND payload ? 'prompt_message_id' AND payload->'original_event_info' ? 'chat_id'
                """
            )
            for row in rows:
                chat_id = int(row['cid'])
                prompt_msg_id = int(row['pmid'])
                self._msg_index[(chat_id, prompt_msg_id)] = (row['user_id'], row['flow'], row['session_id'])
                rebuilt_count += 1
        logger.info(f"Successfully rebuilt message index with {rebuilt_count} entries.")

    async def create(self, user_id: int, flow: Flow, state: str, payload: Optional[Dict[str, Any]] = None,
                     ttl_seconds: Optional[int] = None, single_active: bool = False) -> Session:
        sid = secrets.token_urlsafe(8)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds) if ttl_seconds else None

        async with self.pool.acquire() as conn, conn.transaction():
            if single_active:
                await conn.execute(
                    "UPDATE sessions SET expires_at = NOW() WHERE user_id = $1 AND flow = $2 AND expires_at > NOW()",
                    user_id, flow.value
                )
            new_row = await conn.fetchrow(
                """
                INSERT INTO sessions (user_id, flow, session_id, state, payload, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
                """,
                user_id, flow.value, sid, state, json.dumps(payload or {}), expires_at
            )
        return Session(new_row)

    async def mark_message(self, user_id: int, flow: Flow, session_id: str, chat_id: int, message_id: int):
        """Associates a message with a session, both in memory and in the database."""
        async with self._msg_index_lock:
            self._msg_index[(chat_id, message_id)] = (user_id, flow.value, session_id)
        
        # Update the jsonb payload in the database
        # Using jsonb_set to safely update nested keys
        await self.pool.execute(
            """
            UPDATE sessions
            SET payload = jsonb_set(
                            jsonb_set(payload, '{prompt_message_id}', $1::jsonb),
                            '{original_event_info,chat_id}', $2::jsonb
                        )
            WHERE user_id = $3 AND flow = $4 AND session_id = $5
            """,
            json.dumps(message_id), json.dumps(chat_id), user_id, flow.value, session_id
        )


    async def get(self, user_id: int, flow: Flow, session_id: str) -> Session | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM sessions WHERE user_id = $1 AND flow = $2 AND session_id = $3 AND (expires_at IS NULL OR expires_at > NOW())",
            user_id, flow.value, session_id
        )
        return Session(row) if row else None

    async def get_all_user_sessions(self, user_id: int) -> Dict[str, List[Session]]:
        """Retrieves all active sessions for a user, grouped by flow."""
        rows = await self.pool.fetch(
            "SELECT * FROM sessions WHERE user_id = $1 AND (expires_at IS NULL OR expires_at > NOW())",
            user_id
        )
        sessions_by_flow = {}
        for row in rows:
            session = Session(row)
            sessions_by_flow.setdefault(session.flow.value, []).append(session)
        return sessions_by_flow

    async def update(self, user_id: int, flow: Flow, session_id: str, *, state: str | None = None,
                     payload_mutator=None, ttl_seconds: Optional[int] = None):
        """Updates a session in the database."""
        async with self.pool.acquire() as conn, conn.transaction():
            # using FOR UPDATE to lock the row preventing race conditions
            row  = await conn.fetchrow(
                "SELECT payload FROM sessions WHERE user_id = $1 AND flow = $2 AND session_id = $3 AND expires_at > NOW() FOR UPDATE",
                user_id, flow.value, session_id
            )
            if not row: return

            current_payload = json.loads(row['payload'])
            if payload_mutator:
                payload_mutator(current_payload)

            updates = []
            params = []
            placeholder_idx = 1

            updates.append(f"payload = ${placeholder_idx}")
            params.append(json.dumps(current_payload))
            placeholder_idx += 1

            if state:
                updates.append(f"state = ${placeholder_idx}")
                params.append(state)
                placeholder_idx += 1
            if ttl_seconds:
                updates.append(f"expires_at = ${placeholder_idx}")
                params.append(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))
                placeholder_idx += 1
            
            where_clause = f" WHERE user_id = ${placeholder_idx} AND flow = ${placeholder_idx+1} AND session_id = ${placeholder_idx+2}"
            params.extend([user_id, flow.value, session_id])
            
            query = f"UPDATE sessions SET {', '.join(updates)}{where_clause}"
            
            await conn.execute(query, *params)

    async def expire(self, user_id: int, flow: Flow, session_id: str):
        """Expires a session by setting its expiry date to now."""
        # First get the message details from the session's payload before we update the data base
        session_to_expire = await self.get(user_id, flow, session_id)
        if session_to_expire and session_to_expire.payload:
            prompt_message_id = session_to_expire.payload.get('prompt_message_id')
            # The chat_id for the prompt is stored in a nested dict
            original_event_info = session_to_expire.payload.get('original_event_info', {})
            chat_id = original_event_info.get('chat_id')

            if chat_id and prompt_message_id:
                async with self._msg_index_lock:
                    self._msg_index.pop((chat_id, prompt_message_id), None)
                    logger.debug(f"Removed message ({chat_id}, {prompt_message_id}) from session index.")
        
        await self.pool.execute(
            "UPDATE sessions SET expires_at = NOW() WHERE user_id = $1 AND flow = $2 AND session_id = $3",
            user_id, flow.value, session_id
        )

    async def from_reply(self, chat_id: int, reply_to_msg_id: int) -> Optional[tuple]:
        """Check the in-memory index for a reply."""
        async with self._msg_index_lock:
            return self._msg_index.get((chat_id, reply_to_msg_id))

    async def cleanup(self):
        """
        Deletes old, expired sessions from the database AND the in-memory index.
        """
        delete_threshold = datetime.now(timezone.utc) - timedelta(hours=12)
        
        sessions_to_clean = await self.pool.fetch(
            """
            DELETE FROM sessions 
            WHERE expires_at IS NOT NULL AND expires_at < $1
            RETURNING payload
            """,
            delete_threshold
        )
        deleted_count = len(sessions_to_clean)
        
        if sessions_to_clean:
            async with self._msg_index_lock:
                cleaned_from_index = 0
                for row in sessions_to_clean:
                    payload = json.loads(row['payload'])
                    if not payload: continue
                    
                    prompt_message_id = payload.get('prompt_message_id')
                    original_event_info = payload.get('original_event_info', {})
                    chat_id = original_event_info.get('chat_id')
                    
                    if chat_id and prompt_message_id:
                        if self._msg_index.pop((int(chat_id), int(prompt_message_id)), None):
                            cleaned_from_index += 1
            if cleaned_from_index > 0:
                logger.info(f"Cleaned up {cleaned_from_index} stale session(s) from the message index.")

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old session(s) from the database.")
            
# Global session manager instance
session_manager = SessionManager()