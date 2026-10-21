"""Local page operations on the current workspace's Skill catalog."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import httpx

from core.workspaces import tool_token
from harness.capabilities.catalog import package_catalog
from harness.capabilities.skill_catalog import SkillCatalog
from harness.infrastructure.capability_store import CapabilityStore
from core import config
from .deps import get_chat_service, get_runtime

router = APIRouter()


def catalog_for(runtime, workspace_id, *, allow_write=True):
    manager = getattr(runtime, 'capabilities', None)
    available = [row['id'] for row in package_catalog(config._PROJECT_ROOT / 'plugins',
                 runtime.tool_registry.ownership()) if row['registration'] == 'registered' and
                 all(not manager or not manager.manages(owner) or manager.status(owner)['accepting']
                     for owner in row.get('owners', []))]
    permissions = ['workspace.read'] + (['workspace.write', 'scripts.execute'] if allow_write else [])
    return SkillCatalog(CapabilityStore(runtime.store._connect), workspace_id, available, permissions)


async def snapshot_for(workspace_id):
    service = get_chat_service().for_workspace(workspace_id)
    record = service.workspaces.get(workspace_id)
    await service.workspaces.verify(record)
    async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
        response = await client.post(f"http://127.0.0.1:{record['runner_port']}/capabilities/skills/freeze",
            headers={'X-Workspace-Token': tool_token(workspace_id)})
        response.raise_for_status()
        return response.json()


@router.get('/api/workspaces/{workspace_id}/skills')
async def skills(workspace_id: str):
    try:
        snapshot = await snapshot_for(workspace_id)
        catalog = catalog_for(get_runtime(), workspace_id)
        states = catalog.reconcile(snapshot)
        return {'skills': [{'id': name, **state, 'blockers': catalog.blockers(state.get('pending') or state.get('active') or {})}
                           for name, state in states.items()], 'invalid': snapshot['invalid']}
    except httpx.HTTPError as exc:
        raise HTTPException(503, '技能服务不可用或版本过旧，请通过唯一入口重建工具镜像') from exc


class SkillAction(BaseModel):
    action: str
    revision: str | None = None


class SkillImport(BaseModel):
    path: str


@router.get('/api/workspaces/{workspace_id}/skills/{name}/diff')
async def skill_diff(workspace_id: str, name: str, after: str):
    service = get_chat_service().for_workspace(workspace_id)
    record = service.workspaces.get(workspace_id)
    state = CapabilityStore(get_runtime().store._connect).list(workspace_id).get(name)
    if not state or after not in {row['revision'] for row in
            [state.get('active'), state.get('pending'), *state['history']] if row}:
        raise HTTPException(404, '版本不在本工作区的技能记录中')
    await service.workspaces.verify(record)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            response = await client.post(f"http://127.0.0.1:{record['runner_port']}/capabilities/skills/diff",
                headers={'X-Workspace-Token': tool_token(workspace_id)}, json={'name': name,
                'before': (state.get('active') or {}).get('revision'), 'after': after})
            if response.is_error:
                raise HTTPException(response.status_code, '无法核验版本内容，未生成差异')
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, '技能服务不可用，请稍后重试查看差异') from exc


@router.post('/api/workspaces/{workspace_id}/skills-import')
async def import_skill(workspace_id: str, body: SkillImport, request: Request):
    from .app import require_local_control
    require_local_control(request)
    service = get_chat_service().for_workspace(workspace_id)
    snapshot = service.workspaces.snapshot(workspace_id)
    path = snapshot.virtual(snapshot.resolve(body.path))
    lease = service.workspaces.acquire(snapshot, '__skills__')
    try:
        await service.workspaces.verify(snapshot.record)
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            response = await client.post(f"http://127.0.0.1:{snapshot.record['runner_port']}/capabilities/skills/import",
                headers={'X-Workspace-Token': tool_token(workspace_id)}, json={'path': path})
            if response.is_error:
                raise HTTPException(response.status_code, response.json().get('detail', '技能导入失败'))
            return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(503, '技能服务不可用，未确认导入结果；请刷新目录核对') from exc
    finally:
        service.workspaces.active.pop(lease, None)


@router.post('/api/workspaces/{workspace_id}/skills/{name}')
async def change_skill(workspace_id: str, name: str, body: SkillAction, request: Request):
    from .app import require_local_control
    require_local_control(request)
    service = get_chat_service().for_workspace(workspace_id)
    lease = service.workspaces.acquire(service.workspaces.snapshot(workspace_id), '__skills__')
    try:
        catalog = catalog_for(get_runtime(), workspace_id)
        environment_errors = None
        if body.action in ('activate', 'rollback'):
            states = catalog.reconcile(await snapshot_for(workspace_id))
            state = states.get(name, {})
            if body.revision not in {row['revision'] for row in
                    [state.get('active'), state.get('pending'), *state.get('history', [])] if row}:
                raise ValueError('版本不在此技能的已登记历史中')
            record = service.workspaces.get(workspace_id)
            async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
                response = await client.post(f"http://127.0.0.1:{record['runner_port']}/capabilities/skills/read",
                    headers={'X-Workspace-Token': tool_token(workspace_id)},
                    json={'name': name, 'revision': body.revision})
                response.raise_for_status()
                environment_errors = response.json().get('environment_errors', [])
        return catalog.action(name, body.action, body.revision, environment_errors=environment_errors)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, '技能环境核验失败，未切换版本') from exc
    finally:
        service.workspaces.active.pop(lease, None)
