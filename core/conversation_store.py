"""
SQLite conversation store — replaces Open WebUI for conversation persistence.

Schema
------
conversations(id, title, model, created_at, updated_at)
messages(id, conversation_id, role, content, thinking, tool_calls, tool_name, created_at)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str
    content: str
    thinking: str = ""
    tool_calls: str = ""   # JSON-serialised
    tool_name: str = ""
    created_at: str = ""


@dataclass
class Conversation:
    id: str
    title: str
    model: str
    created_at: str
    updated_at: str
    messages: list[Message] = field(default_factory=list)


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '未命名对话',
    model      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    thinking        TEXT NOT NULL DEFAULT '',
    tool_calls      TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
"""


class ConversationStore:
    """Thread-safe SQLite store for conversations and messages."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or config.DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Conversation CRUD ───────────────────────────────────────────────

    def create_conversation(
        self, *, title: str = "未命名对话", model: str = ""
    ) -> Conversation:
        conv_id = uuid.uuid4().hex[:16]
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, title, model, now, now),
            )
        return Conversation(id=conv_id, title=title, model=model, created_at=now, updated_at=now)

    def get_conversation(self, conv_id: str) -> Conversation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            if not row:
                return None
            conv = Conversation(
                id=row["id"],
                title=row["title"],
                model=row["model"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            msgs = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conv_id,),
            ).fetchall()
            conv.messages = [
                Message(
                    id=m["id"],
                    conversation_id=m["conversation_id"],
                    role=m["role"],
                    content=m["content"],
                    thinking=m["thinking"],
                    tool_calls=m["tool_calls"],
                    tool_name=m["tool_name"],
                    created_at=m["created_at"],
                )
                for m in msgs
            ]
            return conv

    def list_conversations(self, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [
                Conversation(
                    id=r["id"],
                    title=r["title"],
                    model=r["model"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def update_conversation_title(self, conv_id: str, title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, self._now(), conv_id),
            )

    def delete_conversation(self, conv_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    # ── Message CRUD ────────────────────────────────────────────────────

    def add_message(
        self,
        conv_id: str,
        *,
        role: str,
        content: str = "",
        thinking: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        tool_name: str = "",
    ) -> Message:
        msg_id = uuid.uuid4().hex[:16]
        now = self._now()
        tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else ""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, thinking, "
                "tool_calls, tool_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (msg_id, conv_id, role, content, thinking, tc_json, tool_name, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
        return Message(
            id=msg_id,
            conversation_id=conv_id,
            role=role,
            content=content,
            thinking=thinking,
            tool_calls=tc_json,
            tool_name=tool_name,
            created_at=now,
        )

    def get_messages(self, conv_id: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conv_id,),
            ).fetchall()
            return [
                Message(
                    id=r["id"],
                    conversation_id=r["conversation_id"],
                    role=r["role"],
                    content=r["content"],
                    thinking=r["thinking"],
                    tool_calls=r["tool_calls"],
                    tool_name=r["tool_name"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def messages_as_dicts(self, conv_id: str) -> list[dict[str, str]]:
        """Return messages in the format expected by the LLM client."""
        msgs = self.get_messages(conv_id)
        result: list[dict[str, str]] = []
        for m in msgs:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                try:
                    d["tool_calls"] = json.loads(m.tool_calls)
                except json.JSONDecodeError:
                    pass
            if m.tool_name:
                d["tool_name"] = m.tool_name
            result.append(d)
        return result
