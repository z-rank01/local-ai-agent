import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
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

    def list_items(self) -> list[dict]:
        items: list[dict] = []
        for manifest_path in self._trash.glob("*/*/manifest.json"):
            record = self._load_manifest(manifest_path)
            if record is None:
                continue

            relative_path = str(record.get("relative_path") or "")
            trash_path = str(record.get("trash_path") or "")
            item_type = str(record.get("item_type") or "file")
            items.append(
                {
                    "operation_id": str(record.get("operation_id") or manifest_path.parent.name),
                    "deleted_at": str(record.get("deleted_at") or ""),
                    "name": Path(relative_path or trash_path).name,
                    "relative_path": relative_path,
                    "workspace_path": str(record.get("workspace_path") or ""),
                    "original_path": str(record.get("original_path") or ""),
                    "trash_path": trash_path,
                    "item_type": item_type,
                    "exists_in_trash": Path(trash_path).exists(),
                }
            )

        items.sort(key=lambda item: item.get("deleted_at", ""), reverse=True)
        return items

    def restore_from_trash(self, operation_id: str) -> dict:
        manifest_path = self._find_manifest(operation_id)
        record = self._load_manifest(manifest_path)
        if record is None:
            raise FileNotFoundError(f"Trash manifest not found for operation: {operation_id!r}")

        trash_path = Path(str(record.get("trash_path") or ""))
        original_path = Path(str(record.get("original_path") or ""))
        if not trash_path.exists():
            raise FileNotFoundError(f"Trash item not found for operation: {operation_id!r}")
        if not str(original_path):
            raise FileNotFoundError(f"Original path missing in manifest: {operation_id!r}")
        if original_path.exists():
            raise FileExistsError(f"Cannot restore because the original path already exists: {original_path}")

        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_path), str(original_path))

        operation_dir = manifest_path.parent
        manifest_path.unlink(missing_ok=False)
        self._prune_empty_dirs(trash_path.parent, stop_at=operation_dir)
        self._prune_empty_dirs(operation_dir, stop_at=self._trash)

        return {
            "operation_id": operation_id,
            "restored_to": str(original_path),
            "workspace_path": str(record.get("workspace_path") or ""),
            "deleted_at": str(record.get("deleted_at") or ""),
        }

    def cleanup_expired(self, retention_days: int, *, now: datetime | None = None) -> dict:
        if retention_days <= 0:
            return {"removed": 0, "retention_days": retention_days, "operation_ids": []}

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        cutoff = current_time - timedelta(days=retention_days)
        removed_operation_ids: list[str] = []

        for manifest_path in list(self._trash.glob("*/*/manifest.json")):
            record = self._load_manifest(manifest_path)
            if record is None:
                continue
            deleted_at = self._parse_timestamp(record.get("deleted_at"))
            if deleted_at is None or deleted_at > cutoff:
                continue

            operation_dir = manifest_path.parent
            operation_id = str(record.get("operation_id") or operation_dir.name)
            shutil.rmtree(operation_dir, ignore_errors=True)
            self._prune_empty_dirs(operation_dir.parent, stop_at=self._trash)
            removed_operation_ids.append(operation_id)

        return {
            "removed": len(removed_operation_ids),
            "retention_days": retention_days,
            "operation_ids": removed_operation_ids,
        }

    def _write_manifest(self, operation_dir: Path, record: dict) -> None:
        manifest_path = operation_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_manifest(self, operation_id: str) -> Path:
        if not operation_id or not operation_id.strip():
            raise FileNotFoundError("operation_id must be a non-empty string")

        for manifest_path in self._trash.glob(f"*/{operation_id}/manifest.json"):
            return manifest_path
        raise FileNotFoundError(f"Trash operation not found: {operation_id!r}")

    @staticmethod
    def _load_manifest(manifest_path: Path) -> dict | None:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
        current = start
        while current.exists() and current != stop_at:
            try:
                next(current.iterdir())
                return
            except StopIteration:
                current.rmdir()
                current = current.parent

    @staticmethod
    def _to_workspace_path(relative_path: Path) -> str:
        return f"/workspace/{relative_path.as_posix()}"
