import json
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from path_guard import PathGuard


class TrashManager:
    """Soft-deletes workspace items into dated operation folders with metadata."""

    _LEGACY_ENTRY_PATTERN = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)_(?P<name>.+)$")

    def __init__(self, workspace_root: str, trash_root: str, audit_log_path: str | None = None):
        self._guard = PathGuard(workspace_root)
        self._workspace_root = Path(workspace_root).resolve()
        self._trash = Path(trash_root).resolve()
        self._audit_log_path = Path(audit_log_path).resolve() if audit_log_path else None
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

        for legacy_entry in self._iter_legacy_entries():
            items.append(self._legacy_record_for_entry(legacy_entry))

        items.sort(key=lambda item: item.get("deleted_at", ""), reverse=True)
        return items

    def restore_from_trash(self, operation_id: str) -> dict:
        try:
            manifest_path = self._find_manifest(operation_id)
        except FileNotFoundError:
            return self._restore_legacy_entry(operation_id)

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

        for legacy_entry in list(self._iter_legacy_entries()):
            record = self._legacy_record_for_entry(legacy_entry)
            deleted_at = self._parse_timestamp(record.get("deleted_at"))
            if deleted_at is None or deleted_at > cutoff:
                continue

            operation_id = str(record.get("operation_id") or legacy_entry.name)
            if legacy_entry.is_dir():
                shutil.rmtree(legacy_entry, ignore_errors=True)
            else:
                legacy_entry.unlink(missing_ok=True)
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

    def _restore_legacy_entry(self, operation_id: str) -> dict:
        legacy_entry = self._trash / operation_id
        if not legacy_entry.exists():
            raise FileNotFoundError(f"Trash operation not found: {operation_id!r}")

        record = self._legacy_record_for_entry(legacy_entry)
        workspace_path = str(record.get("workspace_path") or "")
        original_path = self._legacy_original_path(record)
        if original_path is None:
            raise FileNotFoundError(f"Original path missing for legacy trash item: {operation_id!r}")
        if original_path.exists():
            raise FileExistsError(f"Cannot restore because the original path already exists: {original_path}")

        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_entry), str(original_path))
        return {
            "operation_id": operation_id,
            "restored_to": str(original_path),
            "workspace_path": workspace_path,
            "deleted_at": str(record.get("deleted_at") or ""),
        }

    def _iter_legacy_entries(self):
        for child in self._trash.iterdir():
            if child.name == "manifest.json":
                continue
            if child.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name):
                continue
            if self._LEGACY_ENTRY_PATTERN.match(child.name):
                yield child

    def _legacy_record_for_entry(self, legacy_entry: Path) -> dict:
        matched = self._match_legacy_audit_record(legacy_entry)
        operation_id = legacy_entry.name
        deleted_at = matched.get("deleted_at") if matched else None
        workspace_path = matched.get("workspace_path") if matched else ""
        original_path = matched.get("original_path") if matched else ""
        relative_path = matched.get("relative_path") if matched else self._legacy_display_name(legacy_entry.name)
        if not deleted_at:
            parsed = self._parse_legacy_entry_timestamp(legacy_entry.name)
            deleted_at = parsed.isoformat() if parsed else ""

        return {
            "operation_id": operation_id,
            "deleted_at": deleted_at or "",
            "name": Path(relative_path or legacy_entry.name).name,
            "relative_path": relative_path,
            "workspace_path": workspace_path,
            "original_path": original_path,
            "trash_path": str(legacy_entry),
            "item_type": "directory" if legacy_entry.is_dir() else "file",
            "exists_in_trash": legacy_entry.exists(),
        }

    def _match_legacy_audit_record(self, legacy_entry: Path) -> dict | None:
        legacy_name = self._legacy_display_name(legacy_entry.name)
        deleted_at = self._parse_legacy_entry_timestamp(legacy_entry.name)
        candidates: list[tuple[float, dict]] = []

        for record in self._iter_audit_file_delete_records():
            workspace_path = str(record.get("workspace_path") or "")
            if not workspace_path:
                continue

            candidate_name = Path(workspace_path).name
            if candidate_name != legacy_name and candidate_name != self._strip_legacy_counter_suffix(legacy_name):
                continue

            audit_ts = self._parse_timestamp(record.get("deleted_at"))
            distance = abs((audit_ts - deleted_at).total_seconds()) if audit_ts and deleted_at else 0.0
            candidates.append((distance, record))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _iter_audit_file_delete_records(self):
        if not self._audit_log_path or not self._audit_log_path.exists():
            return []

        records: list[dict] = []
        try:
            for line in self._audit_log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict) or payload.get("event") != "file_delete":
                    continue
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                workspace_path = params.get("path") if isinstance(params.get("path"), str) else ""
                if not workspace_path:
                    continue
                relative_path = self._workspace_relative_from_workspace_path(workspace_path)
                if relative_path is None:
                    continue
                records.append(
                    {
                        "deleted_at": payload.get("ts"),
                        "workspace_path": workspace_path,
                        "relative_path": relative_path.as_posix(),
                        "original_path": str(self._workspace_root / relative_path),
                    }
                )
        except OSError:
            return []
        return records

    def _legacy_original_path(self, record: dict) -> Path | None:
        original_path = record.get("original_path")
        if not isinstance(original_path, str) or not original_path:
            return None
        return Path(original_path)

    @classmethod
    def _legacy_display_name(cls, entry_name: str) -> str:
        match = cls._LEGACY_ENTRY_PATTERN.match(entry_name)
        return match.group("name") if match else entry_name

    @classmethod
    def _parse_legacy_entry_timestamp(cls, entry_name: str) -> datetime | None:
        match = cls._LEGACY_ENTRY_PATTERN.match(entry_name)
        if not match:
            return None
        try:
            return datetime.strptime(match.group("ts"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _strip_legacy_counter_suffix(value: str) -> str:
        return re.sub(r"_\d+$", "", value)

    @staticmethod
    def _workspace_relative_from_workspace_path(workspace_path: str) -> Path | None:
        normalized = workspace_path.replace("\\", "/")
        if not normalized.startswith("/workspace/"):
            return None
        relative = normalized[len("/workspace/") :]
        return Path(relative)

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
