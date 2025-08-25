# --- Session system (universal, scoped, session-based) ---

from dataclasses import dataclass, field
import secrets
from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio

class Flow(str, Enum):
    CONTACT = "c"
    CUSTOMIZE = "cz"
    ADDCACHE = "ac"

@dataclass
class Session:
    session_id: str
    state: str
    payload: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    active: bool = True

class SessionManager:
    def __init__(self):
        # user_id -> flow -> session_id -> Session
        self._store: Dict[int, Dict[str, Dict[str, Session]]] = {}
        # (chat_id, message_id) -> (user_id, flow, session_id)
        self._msg_index: Dict[tuple, tuple] = {}
        self._lock = asyncio.Lock()

    def _now(self): return datetime.now()

    async def create(self, user_id: int, flow: Flow, state: str, payload: Optional[Dict[str, Any]] = None,
                     ttl_seconds: Optional[int] = None, single_active: bool = False) -> Session:
        sid = secrets.token_urlsafe(8)  # short & safe for callback data
        session = Session(session_id=sid, state=state, payload=payload or {})
        if ttl_seconds:
            session.expires_at = self._now() + timedelta(seconds=ttl_seconds)
        async with self._lock:
            user_map = self._store.setdefault(user_id, {})
            flow_map = user_map.setdefault(flow.value, {})
            if single_active:
                # expire any existing active sessions for this flow
                for old in flow_map.values():
                    old.active = False
            flow_map[sid] = session
        return session

    async def mark_message(self, user_id: int, flow: Flow, session_id: str, chat_id: int, message_id: int):
        async with self._lock:
            self._msg_index[(chat_id, message_id)] = (user_id, flow.value, session_id)

    async def get(self, user_id: int, flow: Flow, session_id: str) -> Optional[Session]:
        async with self._lock:
            return self._store.get(user_id, {}).get(flow.value, {}).get(session_id)

    async def get_active_latest(self, user_id: int, flow: Flow) -> Optional[Session]:
        async with self._lock:
            flow_map = self._store.get(user_id, {}).get(flow.value, {})
            # latest by created_at, still active
            active = [session for session in flow_map.values() if session.active and not self._expired(session)]
            return sorted(active, key=lambda x: x.created_at)[-1] if active else None

    async def get_all_user_sessions(self, user_id: int) -> Dict[str, Dict[str, Session]]:
        """Safely retrieves a copy of all flows and their sessions for a given user."""
        async with self._lock:
            # Return a copy to prevent mutation of the internal store outside the lock
            user_map = self._store.get(user_id, {})
            return {flow: sessions.copy() for flow, sessions in user_map.items()}

    async def update(self, user_id: int, flow: Flow, session_id: str, *, state: Optional[str] = None,
                     payload_mutator=None, ttl_seconds: Optional[int] = None):
        async with self._lock:
            session = self._store.get(user_id, {}).get(flow.value, {}).get(session_id)
            if not session:
                return None
            if state is not None:
                session.state = state
            if payload_mutator:
                payload_mutator(session.payload)
            if ttl_seconds is not None:
                session.expires_at = self._now() + timedelta(seconds=ttl_seconds)
            return session

    async def expire(self, user_id: int, flow: Flow, session_id: str):
        async with self._lock:
            session = self._store.get(user_id, {}).get(flow.value, {}).get(session_id)
            if session:
                session.active = False

    async def expire_flow(self, user_id: int, flow: Flow):
        async with self._lock:
            flow_map = self._store.get(user_id, {}).get(flow.value, {})
            for session in flow_map.values():
                session.active = False

    async def from_reply(self, chat_id: int, reply_to_msg_id: int) -> Optional[tuple]:
        """Return (user_id, flow, session_id) if the reply maps to a session."""
        async with self._lock:
            return self._msg_index.get((chat_id, reply_to_msg_id))

    def _expired(self, session: Session) -> bool:
        return bool(session.expires_at and self._now() > session.expires_at)

    async def expire_if_due(self, session: Session):
        if self._expired(session):
            session.active = False

    async def cleanup(self):
        """
        Expires sessions past their TTL and cleans up old, inactive session data
        and message indexes to prevent memory leaks.
        """
        async with self._lock:
            now = self._now()
            
            # 1) First expire any active sessions that are past their TTL
            for user_flows in self._store.values():
                for flow_sessions in user_flows.values():
                    for session in flow_sessions.values():
                        if session.active and session.expires_at and now > session.expires_at:
                            session.active = False
            
            # 2) Clean up message index for inactive or deleted sessions
            to_del_idx = []
            for msg_key, (uid, flow, sid) in list(self._msg_index.items()):
                sess = self._store.get(uid, {}).get(flow, {}).get(sid)
                if not sess or not sess.active:
                    to_del_idx.append(msg_key)
            
            for key in to_del_idx:
                self._msg_index.pop(key, None)

            # 3) Delete old inactive sessions from the main store
            delete_threshold = now - timedelta(hours=12)
            
            for uid, user_flows in list(self._store.items()):
                for flow, flow_sessions in list(user_flows.items()):
                    for sid, session in list(flow_sessions.items()):
                        # Only remove if inactive AND its expiry time has exceeded threshold
                        if not session.active:
                            expired_with_ttl = session.expires_at and session.expires_at < delete_threshold # has an expiry
                            expired_without_ttl = not session.expires_at and session.created_at < delete_threshold # lacks expiry time
                            if expired_with_ttl or expired_without_ttl:
                                del flow_sessions[sid]
                    
                    if not flow_sessions: # If a flow is now empty remove it
                        del user_flows[flow]
                
                if not user_flows: # If a user has no flows left remove user
                    del self._store[uid]

