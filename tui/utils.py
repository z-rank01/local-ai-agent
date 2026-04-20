"""TUI utility helpers — path detection, URL detection, time formatting."""

from __future__ import annotations

import re
from datetime import datetime, timezone

# Windows or Unix absolute path patterns
_PATH_RE = re.compile(
    r'(?:[A-Za-z]:\\[\w\\. -]+|/[\w/. -]+\.\w{1,6})'
)
# URL pattern
_URL_RE = re.compile(r'https?://[^\s<>"\']+')


def detect_file_paths(text: str) -> list[str]:
    """Extract file paths from text."""
    return _PATH_RE.findall(text)


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
