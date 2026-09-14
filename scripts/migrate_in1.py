"""Merge the legacy IN1 conversation database into the daily database.

One-off migration for the single-entry unification: the IN1 isolated launch
mode used its own SQLite files under data/in1/. This script imports every
conversation (with all messages) from the source DB into the daily DB,
skipping conversations whose id already exists, so it is safe to re-run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def merge(source: Path, target: Path) -> dict:
    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(target)
    try:
        dst.execute("BEGIN")
        imported = skipped = messages_imported = 0
        for conv in src.execute("SELECT * FROM conversations ORDER BY created_at"):
            exists = dst.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conv["id"],)).fetchone()
            if exists:
                skipped += 1
                continue
            conv_cols = table_columns(dst, "conversations")
            values = [conv[col] for col in conv_cols]
            dst.execute(
                f"INSERT INTO conversations ({', '.join(conv_cols)}) "
                f"VALUES ({', '.join('?' for _ in conv_cols)})", values)
            msg_cols_src = table_columns(src, "messages")
            msg_cols_dst = table_columns(dst, "messages")
            common = [c for c in msg_cols_dst if c in msg_cols_src]
            rows = src.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conv["id"],)).fetchall()
            for row in rows:
                dst.execute(
                    f"INSERT INTO messages ({', '.join(common)}) "
                    f"VALUES ({', '.join('?' for _ in common)})",
                    [row[col] for col in common])
            messages_imported += len(rows)
            imported += 1
        dst.commit()
        return {"imported": imported, "skipped": skipped,
                "messages": messages_imported}
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT / "data" / "in1" / "conversations.db"))
    parser.add_argument("--target", default=str(ROOT / "data" / "conversations.db"))
    args = parser.parse_args()
    source, target = Path(args.source), Path(args.target)
    if not source.exists():
        print(f"source database not found: {source}")
        return 1
    if source.resolve() == target.resolve():
        print("source and target must differ")
        return 1
    stats = merge(source, target)
    print(f"imported {stats['imported']} conversations "
          f"({stats['messages']} messages), skipped {stats['skipped']} existing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
