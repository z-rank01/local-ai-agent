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
    tool_result: str = ""
    response_to_message_id: str = ""
    version_number: int = 1
    active: bool = True
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

# Migration steps applied incrementally based on PRAGMA user_version.
# Append new entries here; never edit a released migration.
_MIGRATIONS: list[str] = [
    # version 1: baseline (handled via _SCHEMA, kept as a no-op for clarity)
    "SELECT 1;",
    "SELECT 1;",
    "SELECT 1;",
]


class ConversationStore:
    """Thread-safe SQLite store for conversations and messages."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or config.DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0] or 0
        target = len(_MIGRATIONS)
        for index in range(current, target):
            if index == 1:
                self._migrate_message_versions(conn)
            elif index == 2:
                self._migrate_tool_result_storage(conn)
            else:
                conn.executescript(_MIGRATIONS[index])
        if current != target:
            conn.execute(f"PRAGMA user_version = {target}")

    @staticmethod
    def _migrate_message_versions(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if not columns:
            return
        if "response_to_message_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN response_to_message_id TEXT NOT NULL DEFAULT ''")
        if "version_number" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN version_number INTEGER NOT NULL DEFAULT 1")
        if "active" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_active ON messages(conversation_id, active, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_response_version ON messages(conversation_id, response_to_message_id, version_number)"
        )

    @staticmethod
    def _migrate_tool_result_storage(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if columns and "tool_result" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN tool_result TEXT NOT NULL DEFAULT ''")

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
                "SELECT * FROM messages WHERE conversation_id = ? AND active = 1 ORDER BY created_at",
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
                    tool_result=m["tool_result"],
                    response_to_message_id=m["response_to_message_id"],
                    version_number=m["version_number"],
                    active=bool(m["active"]),
                    created_at=m["created_at"],
                )
                for m in msgs
            ]
            return conv

    def list_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> list[Conversation]:
        with self._connect() as conn:
            search = (query or "").strip()
            if search:
                pattern = f"%{search}%"
                rows = conn.execute(
                    "SELECT * FROM conversations "
                    "WHERE title LIKE ? COLLATE NOCASE "
                    "   OR EXISTS ("
                    "       SELECT 1 FROM messages "
                    "       WHERE messages.conversation_id = conversations.id "
                    "         AND messages.active = 1 "
                    "         AND messages.content LIKE ? COLLATE NOCASE"
                    "   ) "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (pattern, pattern, limit, offset),
                ).fetchall()
            else:
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
        tool_result: Any | None = None,
        response_to_message_id: str = "",
        version_number: int = 1,
        active: bool = True,
    ) -> Message:
        msg_id = uuid.uuid4().hex[:16]
        now = self._now()
        tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else ""
        tr_json = json.dumps(tool_result, ensure_ascii=False, default=str) if tool_result is not None else ""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, thinking, "
                "tool_calls, tool_name, tool_result, response_to_message_id, version_number, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg_id,
                    conv_id,
                    role,
                    content,
                    thinking,
                    tc_json,
                    tool_name,
                    tr_json,
                    response_to_message_id,
                    version_number,
                    1 if active else 0,
                    now,
                ),
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
            tool_result=tr_json,
            response_to_message_id=response_to_message_id,
            version_number=version_number,
            active=active,
            created_at=now,
        )

    def get_messages(self, conv_id: str, *, include_inactive: bool = False) -> list[Message]:
        with self._connect() as conn:
            sql = "SELECT * FROM messages WHERE conversation_id = ?"
            params: tuple[Any, ...] = (conv_id,)
            if not include_inactive:
                sql += " AND active = 1"
            sql += " ORDER BY created_at"
            rows = conn.execute(
                sql,
                params,
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
                    tool_result=r["tool_result"],
                    response_to_message_id=r["response_to_message_id"],
                    version_number=r["version_number"],
                    active=bool(r["active"]),
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

    def get_message(self, message_id: str) -> Message | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if not row:
                return None
            return Message(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                thinking=row["thinking"],
                tool_calls=row["tool_calls"],
                tool_name=row["tool_name"],
                tool_result=row["tool_result"],
                response_to_message_id=row["response_to_message_id"],
                version_number=row["version_number"],
                active=bool(row["active"]),
                created_at=row["created_at"],
            )

    def update_message_content(self, conv_id: str, message_id: str, content: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE messages SET content = ? WHERE id = ? AND conversation_id = ?",
                (content, message_id, conv_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (self._now(), conv_id),
                )
                return True
            return False

    def delete_message(self, conv_id: str, message_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE id = ? AND conversation_id = ?",
                (message_id, conv_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (self._now(), conv_id),
                )
                return True
            return False

    def delete_messages_from(self, conv_id: str, message_id: str, *, inclusive: bool = True) -> int:
        """Delete the message and everything created after it (inclusive by default)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT created_at FROM messages WHERE id = ? AND conversation_id = ?",
                (message_id, conv_id),
            ).fetchone()
            if not row:
                return 0
            op = ">=" if inclusive else ">"
            cursor = conn.execute(
                f"DELETE FROM messages WHERE conversation_id = ? AND created_at {op} ?",
                (conv_id, row["created_at"]),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (self._now(), conv_id),
                )
            return cursor.rowcount

    def find_last_user_message(self, conv_id: str) -> Message | None:
        for message in reversed(self.get_messages(conv_id)):
            if message.role == "user":
                return message
        return None

    def list_response_versions(self, conv_id: str, response_to_message_id: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND response_to_message_id = ? AND role = 'assistant' ORDER BY version_number, created_at",
                (conv_id, response_to_message_id),
            ).fetchall()
            return [
                Message(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    role=row["role"],
                    content=row["content"],
                    thinking=row["thinking"],
                    tool_calls=row["tool_calls"],
                    tool_name=row["tool_name"],
                    tool_result=row["tool_result"],
                    response_to_message_id=row["response_to_message_id"],
                    version_number=row["version_number"],
                    active=bool(row["active"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def next_response_version_number(self, conv_id: str, response_to_message_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM messages WHERE conversation_id = ? AND response_to_message_id = ? AND role = 'assistant'",
                (conv_id, response_to_message_id),
            ).fetchone()
            return int((row["max_version"] if row else 0) or 0) + 1

    def set_response_version_active(self, conv_id: str, response_to_message_id: str, version_number: int) -> bool:
        with self._connect() as conn:
            target = conn.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ? AND response_to_message_id = ? AND version_number = ? AND role = 'assistant'",
                (conv_id, response_to_message_id, version_number),
            ).fetchone()
            if not target:
                return False
            conn.execute(
                "UPDATE messages SET active = CASE WHEN version_number = ? THEN 1 ELSE 0 END WHERE conversation_id = ? AND response_to_message_id = ?",
                (version_number, conv_id, response_to_message_id),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (self._now(), conv_id),
            )
            return True

    def deactivate_response_versions(self, conv_id: str, response_to_message_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE messages SET active = 0 WHERE conversation_id = ? AND response_to_message_id = ?",
                (conv_id, response_to_message_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (self._now(), conv_id),
                )
            return cursor.rowcount

    def delete_response_versions(self, conv_id: str, response_to_message_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND response_to_message_id = ?",
                (conv_id, response_to_message_id),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (self._now(), conv_id),
                )
            return cursor.rowcount
