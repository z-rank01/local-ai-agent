import shutil
from datetime import datetime, timezone
from pathlib import Path

from path_guard import PathGuard


class TrashManager:
    """Soft-deletes files by moving them to a trash directory with a timestamp prefix."""

    def __init__(self, workspace_root: str, trash_root: str):
        self._guard = PathGuard(workspace_root)
        self._trash = Path(trash_root).resolve()
        self._trash.mkdir(parents=True, exist_ok=True)

    def move_to_trash(self, path: str) -> dict:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path!r}")

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self._trash / f"{ts}_{resolved.name}"

        # Avoid name collision by appending a counter if needed.
        if dest.exists():
            counter = 1
            while dest.exists():
                dest = self._trash / f"{ts}_{resolved.name}_{counter}"
                counter += 1

        shutil.move(str(resolved), str(dest))
        return {"moved_to_trash": str(dest), "original": str(resolved)}
