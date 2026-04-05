import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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

_guard = PathGuard(_WORKSPACE)
_file_ops = FileOps(_guard)
_trash = TrashManager(_WORKSPACE, _TRASH)
_git = GitOps(_WORKSPACE)

_AUTO_GIT = os.environ.get("AUTO_GIT_COMMIT", "true").lower() == "true"

app = FastAPI(title="Skill: Files")


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
        result = _trash.move_to_trash(req.path)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
