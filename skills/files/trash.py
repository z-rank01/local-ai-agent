import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from path_guard import PathGuard


class TrashManager:
    """Soft-deletes workspace items into dated operation folders with metadata."""

    def __init__(self, workspace_root: str, trash_root: str):
        self._guard = PathGuard(workspace_root)
        self._workspace_root = Path(workspace_root).resolve()
        self._trash = Path(trash_root).resolve()
        self._trash.mkdir(parents=True, exist_ok=True)

    def move_to_trash(self, path: str) -> dict:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path!r}")

        deleted_at = datetime.now(timezone.utc)
        ts = deleted_at.strftime("%Y%m%dT%H%M%SZ")
        date_bucket = deleted_at.strftime("%Y-%m-%d")
        operation_id = f"{ts}_{uuid.uuid4().hex[:8]}"
        operation_dir = self._trash / date_bucket / operation_id
        relative_path = resolved.relative_to(self._workspace_root)
        dest = operation_dir / relative_path

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(resolved), str(dest))
        self._write_manifest(
            operation_dir,
            {
                "operation_id": operation_id,
                "deleted_at": deleted_at.isoformat(),
                "original_path": str(resolved),
                "workspace_path": self._to_workspace_path(relative_path),
                "relative_path": relative_path.as_posix(),
                "trash_path": str(dest),
                "item_type": "directory" if dest.is_dir() else "file",
            },
        )
        return {
            "moved_to_trash": str(dest),
            "original": str(resolved),
            "operation_id": operation_id,
            "manifest": str(operation_dir / "manifest.json"),
            "workspace_path": self._to_workspace_path(relative_path),
        }

    def _write_manifest(self, operation_dir: Path, record: dict) -> None:
        manifest_path = operation_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _to_workspace_path(relative_path: Path) -> str:
        return f"/workspace/{relative_path.as_posix()}"
