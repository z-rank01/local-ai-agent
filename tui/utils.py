"""TUI utility helpers — path detection, URL detection, time formatting."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Windows or Unix absolute path patterns
_PATH_RE = re.compile(
    r'(?:[A-Za-z]:\\[\w\\. -]+|/[\w/. -]+\.\w{1,6})'
)
# URL pattern
_URL_RE = re.compile(r'https?://[^\s<>"\']+')


def detect_file_paths(text: str) -> list[str]:
    """Extract file paths from text."""
    return _PATH_RE.findall(text)


def ingest_local_file_paths(text: str, workspace_root: str | Path) -> tuple[str, list[str]]:
    """Normalize absolute file paths to /workspace paths and import external files.

    Files already inside the workspace are rewritten to their /workspace form.
    """
    workspace_root = Path(workspace_root)
    data_dir = workspace_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    imported: list[str] = []
    rewritten_text = text
    for raw_path in detect_file_paths(text):
        source = Path(raw_path)
        if not source.is_absolute() or not source.exists() or not source.is_file():
            continue

        try:
            source_resolved = source.resolve()
            workspace_resolved = workspace_root.resolve()
            if workspace_resolved in source_resolved.parents:
                relative = source_resolved.relative_to(workspace_resolved).as_posix()
                rewritten_text = rewritten_text.replace(raw_path, f"/workspace/{relative}")
                continue
        except Exception:
            pass

        target = data_dir / source.name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            index = 1
            while True:
                candidate = data_dir / f"{stem}_{index}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                index += 1

        shutil.copy2(source, target)
        imported.append(str(target))
        rewritten_text = rewritten_text.replace(raw_path, f"/workspace/data/{target.name}")

    return rewritten_text, imported


def detect_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    return _URL_RE.findall(text)


def time_ago(iso_str: str) -> str:
    """Convert ISO timestamp to a relative time string like '2h ago'."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "刚刚"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}分钟前"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}小时前"
        days = hours // 24
        if days < 30:
            return f"{days}天前"
        months = days // 30
        return f"{months}个月前"
    except (ValueError, TypeError):
        return ""


def truncate(text: str, length: int = 40) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"
