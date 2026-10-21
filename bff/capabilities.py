"""Authenticated inventory route; mutations remain in their lifecycle owner."""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import httpx
from core import config
from harness.capabilities.catalog import package_catalog
from .deps import get_runtime

router = APIRouter()


@router.get('/api/capabilities')
async def capabilities():
    runtime = get_runtime()
    rows = package_catalog(config._PROJECT_ROOT / 'plugins', runtime.tool_registry.ownership())
    manager = getattr(runtime, 'capabilities', None)
    for row in rows:
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
