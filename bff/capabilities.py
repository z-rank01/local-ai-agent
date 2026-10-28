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
    installs = CapabilityStore(runtime.store._connect).list('__host__', 'plugin_package')
    for row in rows:
        imported = row['id'] in installs
        row['origin'] = 'local' if imported else 'builtin'
        row['scope'] = 'host'
        service = getattr(runtime, 'capability_services', {}).get(row['id'])
        row['actions'] = {
            'toggle': row.get('management') == 'managed' or callable(getattr(service, 'set_enabled', None)),
            'uninstall': imported,
            'edit': False,
        }
        row['action_note'] = ('通过源包更新；安装、回退和卸载需正常退出后重启生效' if imported
                              else '内置插件随项目部署更新；支持设置与停用，不提供安装副本卸载')
        row['environment'] = environments.get(row['id'], {'state': 'NOT_PREPARED'})
        adapter_error = getattr(runtime, 'adapter_errors', {}).get(row['id'])
        if adapter_error:
            row['error'] = row.get('error') or '服务适配器不可用：' + adapter_error
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


class CapabilityCommandRequest(BaseModel):
    workspace_id: str = 'default'
    arguments: dict = {}


@router.post('/api/capabilities/{identity}/commands/{name}')
async def execute_command(identity: str, name: str, body: CapabilityCommandRequest, request: Request):
    from .app import require_local_control
    from .deps import get_chat_service
    require_local_control(request)
    try:
        return await get_chat_service().execute_capability_command(
            identity, name, body.arguments, body.workspace_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


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
    row = next((row for row in rows if row['id'] == identity), None)
    service = getattr(runtime, 'capability_services', {}).get(identity)
    setter = getattr(service, 'set_enabled', None)
    if row is None or (row.get('management') != 'managed' and not callable(setter)):
        raise HTTPException(404, '未登记可管理插件')
    manager = runtime.capabilities
    if manager is None or not manager.manages(identity):
        raise HTTPException(409, '插件尚未加载，请通过唯一入口重启')
    if setter:
        from .deps import get_chat_service
        try:
            return await setter(runtime, body.enabled, lambda: bool(get_chat_service()._active_conversations))
        except (ValueError, RuntimeError, OSError, httpx.HTTPError, TimeoutError) as exc:
            raise HTTPException(409, str(exc)) from exc
    store = CapabilityStore(runtime.store._connect)
    # Keep the explicit intent even if activation or drain fails; state shows the error.
    store.save('__host__', identity, {'enabled': body.enabled}, 'plugin')
    try:
        enabled = {name for name, state in store.list('__host__', 'plugin').items() if state.get('enabled')}
        return await (manager.activate(identity, enabled_dependencies=enabled) if body.enabled else manager.deactivate(identity))
    except (ValueError, RuntimeError, httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(409, str(exc)) from exc
