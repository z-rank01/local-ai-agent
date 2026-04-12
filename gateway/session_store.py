"""
Session Store — lightweight in-memory session state for the gateway.

Tracks per-conversation metadata to support context compaction,
memory injection, and token budget tracking across turns.
"""

import hashlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger("gateway.session")


class SessionStore:
    """In-memory per-conversation state store with TTL-based cleanup.

    Sessions are identified by a fingerprint derived from the first few
    messages in a conversation, making them stable across requests from
    Open WebUI (which sends the full history each time).
    """

    def __init__(self, ttl_seconds: int = 7200):
        self._sessions: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(messages: list[dict]) -> str:
        """Derive a stable session ID from early messages."""
        parts: list[str] = []
        for m in messages:
            role = m.get("role", "")
            if role in {"system", "tool"}:
                continue
            content = (m.get("content") or "")[:200]
            if not content.strip():
                continue
            parts.append(f"{role}:{content}")
            if len(parts) >= 6:
                break
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return session data or ``None`` if not found."""
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s["last_used"] = time.time()
            return s

    def upsert(self, session_id: str, **kwargs) -> dict[str, Any]:
        """Get-or-create a session and update it with *kwargs*."""
        with self._lock:
            self._cleanup_locked()
            if session_id not in self._sessions:
                self._sessions[session_id] = {
                    "id": session_id,
                    "compact_count": 0,
                    "token_peak": 0,
                    "created_at": time.time(),
                    "last_used": time.time(),
                }
            session = self._sessions[session_id]
            session.update(kwargs)
            session["last_used"] = time.time()
            return dict(session)

    def _cleanup_locked(self) -> None:
        """Remove sessions older than TTL.  Must be called under lock."""
        now = time.time()
        expired = [
            k
            for k, v in self._sessions.items()
            if now - v["last_used"] > self._ttl
        ]
        for k in expired:
            del self._sessions[k]
        if expired:
            logger.debug("SessionStore: cleaned up %d expired sessions", len(expired))

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)
