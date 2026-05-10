import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yaml

from file_ops import FileOps
from git_ops import GitOps
from path_guard import PathGuard
from trash import TrashManager

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("skill-files")

_WORKSPACE = "/workspace"
_TRASH = "/trash"
_AUDIT_LOG = "/logs/audit.jsonl"
_RUNTIME_CONFIG_PATH = Path(os.environ.get("RUNTIME_CONFIG_PATH", "/config/runtime.yaml"))
_DEFAULT_TRASH_RETENTION_DAYS = 30
_DEFAULT_TRASH_CLEANUP_INTERVAL_SECONDS = 3600

_guard = PathGuard(_WORKSPACE)
_file_ops = FileOps(_guard)
_trash = TrashManager(_WORKSPACE, _TRASH, audit_log_path=_AUDIT_LOG)
_git = GitOps(_WORKSPACE)

_AUTO_GIT = os.environ.get("AUTO_GIT_COMMIT", "true").lower() == "true"


def _coerce_int(value: object, default: int, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, falling back to %s", label, value, default)
        return default


def _load_trash_settings() -> tuple[int, int]:
    runtime_config: dict = {}
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                runtime_config = loaded
        except Exception as exc:
            logger.warning("Failed to load runtime config from %s: %s", _RUNTIME_CONFIG_PATH, exc)

    trash_config = runtime_config.get("trash") if isinstance(runtime_config.get("trash"), dict) else {}
    raw_retention = os.environ.get("TRASH_RETENTION_DAYS", trash_config.get("retention_days", _DEFAULT_TRASH_RETENTION_DAYS))
    raw_interval = os.environ.get(
        "TRASH_CLEANUP_INTERVAL_SECONDS",
        trash_config.get("cleanup_interval_seconds", _DEFAULT_TRASH_CLEANUP_INTERVAL_SECONDS),
    )

    retention_days = max(0, _coerce_int(raw_retention, _DEFAULT_TRASH_RETENTION_DAYS, "TRASH_RETENTION_DAYS"))
    cleanup_interval_seconds = max(
        0,
        _coerce_int(raw_interval, _DEFAULT_TRASH_CLEANUP_INTERVAL_SECONDS, "TRASH_CLEANUP_INTERVAL_SECONDS"),
    )
    return retention_days, cleanup_interval_seconds


_TRASH_RETENTION_DAYS, _TRASH_CLEANUP_INTERVAL_SECONDS = _load_trash_settings()
_last_trash_cleanup_monotonic = 0.0


def _cleanup_trash_if_due(*, force: bool = False) -> None:
    global _last_trash_cleanup_monotonic

    if _TRASH_RETENTION_DAYS <= 0:
        return

    now = time.monotonic()
    if (
        not force
        and _TRASH_CLEANUP_INTERVAL_SECONDS > 0
        and _last_trash_cleanup_monotonic > 0
        and now - _last_trash_cleanup_monotonic < _TRASH_CLEANUP_INTERVAL_SECONDS
    ):
        return

    try:
        result = _trash.cleanup_expired(_TRASH_RETENTION_DAYS)
        _last_trash_cleanup_monotonic = now
        removed = int(result.get("removed", 0))
        if removed:
            logger.info("Trash cleanup removed %s expired operations", removed)
    except Exception:
        logger.exception("Trash cleanup failed")

app = FastAPI(title="Skill: Files")


@app.on_event("startup")
async def startup_cleanup():
    _cleanup_trash_if_due(force=True)


# ── Request models ───────────────────────────────────────────────────────────

class ReadRequest(BaseModel):
    path: str


class WriteRequest(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


class ListRequest(BaseModel):
    directory: str = "/workspace"


class DeleteRequest(BaseModel):
    path: str


class RestoreTrashRequest(BaseModel):
    operation_id: str


class RenameRequest(BaseModel):
    src: str
    dst: str


class CommitRequest(BaseModel):
    message: str | None = None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "workspace": _WORKSPACE}


@app.post("/tool/file_read")
async def file_read(req: ReadRequest):
    try:
        result = _file_ops.read(req.path)
        # result may be a str (text content) or a dict (unsupported binary metadata)
        if isinstance(result, dict):
            return result
        return {"content": result}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("file_read failed for %s", req.path)
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}")


@app.post("/tool/file_write")
async def file_write(req: WriteRequest):
    try:
        result = _file_ops.write(req.path, req.content, req.encoding)
        if _AUTO_GIT:
            _git.auto_commit(f"agent: write {req.path}")
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.post("/tool/file_list")
async def file_list(req: ListRequest):
    try:
        entries = _file_ops.list_dir(req.directory)
        return {"entries": entries}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/tool/file_delete")
async def file_delete(req: DeleteRequest):
    try:
        _cleanup_trash_if_due()
        result = _trash.move_to_trash(req.path)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/trash/items")
async def trash_items():
    try:
        _cleanup_trash_if_due()
        return {"items": _trash.list_items()}
    except Exception as exc:
        logger.exception("trash_items failed")
        raise HTTPException(status_code=500, detail=f"Failed to list trash items: {exc}")


@app.post("/trash/restore")
async def trash_restore(req: RestoreTrashRequest):
    try:
        _cleanup_trash_if_due()
        return _trash.restore_from_trash(req.operation_id)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("trash_restore failed for %s", req.operation_id)
        raise HTTPException(status_code=500, detail=f"Failed to restore trash item: {exc}")


@app.post("/tool/file_rename")
async def file_rename(req: RenameRequest):
    try:
        src = _guard.resolve(req.src)
        dst = _guard.resolve(req.dst)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {req.src!r}")
        if dst.exists():
            raise PermissionError(f"Destination already exists: {req.dst!r}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        if _AUTO_GIT:
            _git.auto_commit(f"agent: rename {req.src} -> {req.dst}")
        return {"renamed": True, "src": str(src), "dst": str(dst)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/tool/git_status")
async def git_status():
    return _git.status()


@app.post("/tool/git_commit")
async def git_commit(req: CommitRequest):
    result = _git.commit(req.message or "agent: manual commit")
    if not result.get("committed"):
        raise HTTPException(status_code=400, detail=result.get("reason", "commit failed"))
    return result
