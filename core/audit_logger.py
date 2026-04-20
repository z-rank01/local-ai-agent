"""
Audit logger — thread-safe JSONL event recorder.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("core.audit")


class AuditLogger:
    def __init__(self, log_path: str | Path):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: str, data: dict[str, Any]) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        try:
            with self._lock:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as exc:
            logger.error("Failed to write audit log: %s", exc)
