"""Authenticated inventory route; mutations remain in their lifecycle owner."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import httpx
from core import config
from harness.capabilities.catalog import package_catalog
from .deps import get_runtime
import asyncio
from harness.infrastructure.capability_store import CapabilityStore

router = APIRouter()


@router.get('/api/capabilities')
async def capabilities():
    runtime = get_runtime()
    rows = package_catalog(config._PROJECT_ROOT / 'plugins', runtime.tool_registry.ownership())
    manager = getattr(runtime, 'capabilities', None)
    environments = CapabilityStore(runtime.store._connect).list('__host__', 'plugin_environment')
    for row in rows:
        row['environment'] = environments.get(row['id'], {'state': 'NOT_PREPARED'})
        prompt_error = getattr(getattr(runtime, 'prompt_builder', None), 'contribution_errors', {}).get(row['id'])
        if prompt_error:
            row['error'] = row.get('error') or '能力提示不可用：' + prompt_error
        artifact_error = getattr(getattr(runtime, 'artifact_providers', None), 'errors', {}).get(row['id'])
        if artifact_error:
            row['error'] = row.get('error') or '成果提供者不可用：' + artifact_error
        row['lifecycle'] = {owner: manager.status(owner) for owner in row.get('owners', [])
                            if manager and manager.manages(owner)}
    return {'api_version': 1, 'capabilities': rows}


class CapabilityAction(BaseModel):
    enabled: bool


_preparations = {}


@router.post('/api/capabilities/{identity}/prepare')
async def prepare_capability(identity: str, request: Request):
    from .app import require_local_control
    require_local_control(request)
    runtime = get_runtime()
    package = (runtime.container_packages or {}).get(identity)
    if package is None:
        raise HTTPException(404, '未登记可准备的插件环境')
    if runtime.capabilities.status(identity)['state'] not in ('INACTIVE', 'START_FAILED'):
        raise HTTPException(409, '先停用插件并完成收尾，再准备环境')
    if identity in _preparations:
        return {'state': 'PREPARING'}
    store = CapabilityStore(runtime.store._connect)
    store.save('__host__', identity, {'state': 'PREPARING'}, 'plugin_environment')
    async def prepare():
        try:
            state = await package.client.prepare()
        except BaseException as exc:
            state = {'state': 'PREPARE_FAILED', 'error': str(exc) or type(exc).__name__}
        store.save('__host__', identity, state, 'plugin_environment')
    task = asyncio.create_task(prepare())
    _preparations[identity] = task
    task.add_done_callback(lambda _: _preparations.pop(identity, None))
    return {'state': 'PREPARING'}


@router.post('/api/capabilities/{identity}')
async def change_capability(identity: str, body: CapabilityAction, request: Request):
    from .app import require_local_control
    from harness.infrastructure.capability_store import CapabilityStore
    require_local_control(request)
    runtime = get_runtime()
    rows = package_catalog(config._PROJECT_ROOT / 'plugins', runtime.tool_registry.ownership())
    if not any(row['id'] == identity and row.get('management') == 'managed' for row in rows):
        raise HTTPException(404, '未登记可管理插件')
    manager = runtime.capabilities
    if manager is None or not manager.manages(identity):
        raise HTTPException(409, '插件尚未加载，请通过唯一入口重启')
    store = CapabilityStore(runtime.store._connect)
    # Keep the explicit intent even if activation or drain fails; state shows the error.
    store.save('__host__', identity, {'enabled': body.enabled}, 'plugin')
    try:
        return await (manager.activate(identity) if body.enabled else manager.deactivate(identity))
    except (ValueError, RuntimeError, httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(409, str(exc)) from exc
