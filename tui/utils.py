"""TUI utility helpers — shared import helpers plus local display utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from core.input_utils import detect_file_paths, detect_urls, ingest_local_file_paths


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
