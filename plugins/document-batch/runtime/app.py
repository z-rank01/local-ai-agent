"""Authenticated optional container API; no model credentials or outbound network."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from path_guard import PathGuard
from harness.contracts.package_revision import package_revision
from .jobs import Jobs

root = Path(os.environ.get('WORKSPACE_ROOT', '/workspace'))
guard = PathGuard(str(root))
jobs = None


@asynccontextmanager
async def lifespan(app):
    global jobs
    jobs = Jobs(root, guard.resolve)
    yield
    await jobs.drain()


app = FastAPI(lifespan=lifespan)


@app.middleware('http')
async def authenticate(request: Request, call_next):
    expected = os.environ.get('WORKSPACE_TOOL_TOKEN', '')
    if not expected or not secrets.compare_digest(request.headers.get('X-Workspace-Token', ''), expected):
        return JSONResponse({'detail': 'Workspace credential required'}, status_code=403)
    try:
        return await call_next(request)
    except (ValueError, PermissionError) as exc:
        return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/health')
async def health():
    return {'workspace_id': os.environ['WORKSPACE_ID'], 'workspace_version': int(os.environ['WORKSPACE_VERSION']),
            'plugin_id': 'document-batch', 'service_id': 'ocr',
            'revision': package_revision(Path(__file__).resolve().parents[1]),
            'accepting': jobs.accepting}


class Invocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    arguments: dict
    context: dict


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paths: list[str] = Field(min_length=1, max_length=50)
    language: str = 'eng+chi_sim'


@app.post('/tools/documents_batch')
async def submit(body: Invocation):
    args = Batch.model_validate(body.arguments)
    if not body.context.get('conversation_id') or not body.context.get('run_id'):
        raise HTTPException(400, 'Host-owned execution context is required')
    return jobs.submit(body.request_id, args.paths, args.language, body.context)


@app.get('/tasks')
async def list_tasks():
    return {'tasks': jobs.records.all()}


@app.get('/activity')
async def activity():
    return {'inflight': len(jobs.pending)}


@app.get('/tasks/{identity}')
async def get_task(identity: str):
    try:
        return jobs.records.get(identity)
    except FileNotFoundError as exc:
        raise HTTPException(404, '任务不存在') from exc


@app.post('/tasks/{identity}/cancel')
async def cancel_task(identity: str):
    return await jobs.cancel(identity)


@app.post('/drain')
async def drain():
    return await jobs.drain()
