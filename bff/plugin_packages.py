"""Local package management; imported content is not executed by these routes."""
import re
import zipfile
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core import config
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_packages import PluginPackages, mutation_lock
from .deps import get_runtime

router = APIRouter()
_mutation = mutation_lock


def packages():
    return PluginPackages(CapabilityStore(get_runtime().store._connect),
        config._PROJECT_ROOT / 'data/private/plugin-packages', config._PROJECT_ROOT / 'plugins')


class ImportPackage(BaseModel):
    path: str


class PackageAction(BaseModel):
    action: str
    revision: str | None = None
    trusted: bool = False


class SourceGrant(BaseModel):
    public_key: str
    permissions: list[str]
    actions: list[str]
    host_scope: bool
    kind: str = 'plugin'
    approved_revision: str | None = None


@router.get('/api/plugin-packages/grants/{workspace_id}')
async def source_grants(workspace_id: str, request: Request):
    from .app import _is_loopback_host
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看来源授权')
    store = packages().store
    return {'grants': [{'identity': identity, **value} for category in ('host_package_grant', 'skill_source_grant')
                       for identity, value in store.list(workspace_id, category).items()]}


@router.put('/api/plugin-packages/grants/{workspace_id}/{identity}')
async def set_package_grant(workspace_id: str, identity: str, body: SourceGrant, request: Request):
    from harness.infrastructure.package_authority import valid_identity
    from .app import require_local_control
    from .deps import get_chat_service
    require_local_control(request)
    get_chat_service().workspaces.get(workspace_id)
    if (body.kind not in ('skill', 'plugin') or body.host_scope != (body.kind == 'plugin')
            or not valid_identity(identity, body.kind)
            or (body.approved_revision is not None and not re.fullmatch('[0-9a-f]{64}', body.approved_revision))
            or not re.fullmatch('[0-9a-f]{64}',body.public_key)
            or set(body.actions)-{'import','install','upgrade','uninstall','status','enable','disable'}):
        raise HTTPException(422,'需要具体宿主包、来源公钥和明确的管理操作')
    async with _mutation:
        store = packages().store
        category = 'host_package_grant' if body.kind == 'plugin' else 'skill_source_grant'
        if body.actions:
            store.save(workspace_id,identity,body.model_dump(),category)
        else:
            store.remove(workspace_id,identity,category)
    return {'identity':identity,'workspace_id':workspace_id,'actions':body.actions}


@router.get('/api/plugin-packages')
async def list_packages():
    return {'packages': [{'id': identity, **state} for identity, state in
                         packages().store.list('__host__', 'plugin_package').items()]}


@router.post('/api/plugin-packages/import')
async def import_package(body: ImportPackage, request: Request):
    from .app import require_local_control
    require_local_control(request)
    source = Path(body.path)
    if not source.is_absolute():
        raise HTTPException(422, '请输入本机插件目录或 ZIP 的完整路径')
    async with _mutation:
        try:
            return await run_in_threadpool(packages().import_source, source)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post('/api/plugin-packages/{identity}')
async def change_package(identity: str, body: PackageAction, request: Request):
    from .app import require_local_control
    require_local_control(request)
    async with _mutation:
        try:
            return await run_in_threadpool(packages().queue, identity, body.action,
                                          body.revision, trusted=body.trusted)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get('/api/plugin-packages/{identity}/diff')
async def diff_package(identity: str, after: str):
    from harness.infrastructure.package_diff import package_diff
    try:
        return await run_in_threadpool(package_diff, packages(), identity, after)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
