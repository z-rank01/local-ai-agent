"""Unified run routes. External task facts and controls remain provider-owned."""
import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from core import config
from harness.contracts.tasks import TaskUnavailable
from harness.runtime.tasks import TaskRegistry
from .deps import get_chat_service, get_runtime
from .schemas import UIStreamEvent

router = APIRouter()


def providers():
    runtime = get_runtime()
    if getattr(runtime, 'task_providers', None) is None:
        runtime.task_providers = TaskRegistry.bundled(config._PROJECT_ROOT / 'plugins')
    return runtime.task_providers


def provider_for(run_id):
    if ':' not in run_id:
        return None
    try:
        return providers().for_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/api/runs')
async def list_runs(request: Request, workspace_id: str | None = None):
    service = get_chat_service()
    if workspace_id:
        service.workspaces.get(workspace_id)
    result = {'runs': [service.run_summary(row) for row in service.runs.store.list(workspace_id)],
              'providers': {}, 'provider_errors': providers().errors,
              'shutdown_error': getattr(request.app.state, 'shutdown_error', ''),
              'page_connections': len(getattr(getattr(request.app.state, 'auto_exit', None), 'pages', ()))}
    for namespace, provider in providers().providers.items():
        try:
            snapshot = await provider.list(workspace_id)
            result['runs'].extend(snapshot['runs'])
            result['providers'][namespace] = snapshot['status']
            if snapshot.get('compatibility_key'):
                result[snapshot['compatibility_key']] = snapshot['status']
        except (ValueError, httpx.HTTPError, OSError) as exc:
            result['providers'][namespace] = {'online': False, 'message': str(exc), 'state': 'UNKNOWN'}
    return result


@router.get('/api/runs/{run_id}')
async def get_run(run_id: str):
    provider = provider_for(run_id)
    if provider is not None:
        try:
            return await provider.get(run_id)
        except TaskUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    service = get_chat_service()
    return service.run_summary(service.runs.store.get(run_id))


@router.get('/api/runs/{run_id}/events')
async def run_events(run_id: str, after_seq: int = Query(0, ge=0)):
    from .app import _stream_ndjson
    if provider_for(run_id) is not None:
        async def snapshots():
            last = None
            while True:
                row = await get_run(run_id)
                if row != last:
                    yield UIStreamEvent(event='run.updated', run_id=run_id, data={'run': row})
                    last = row
                if row['status'] in ('SUCCEEDED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'PAUSED', 'QUEUED'):
                    return
                await asyncio.sleep(1)
        return await _stream_ndjson(snapshots(), conversation_id=None)
    service = get_chat_service()
    service.runs.public(service.runs.store.get(run_id))
    async def events():
        async for event in service.runs.subscribe(run_id, after_seq):
            yield UIStreamEvent.model_validate(event)
    return await _stream_ndjson(events(), conversation_id=None)


@router.post('/api/runs/{run_id}/control')
async def control_run(run_id: str, request: Request):
    from .app import require_local_control
    require_local_control(request)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in ('pause', 'resume', 'stop'):
        raise HTTPException(422, '操作须为pause、resume或stop')
    provider = provider_for(run_id)
    if provider is not None:
        try:
            return await provider.control(run_id, body['action'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return await get_chat_service().control_run(run_id, body['action'])
