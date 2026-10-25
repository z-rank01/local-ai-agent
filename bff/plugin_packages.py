"""Local package management; imported content is not executed by these routes."""
import asyncio
import zipfile
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core import config
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_packages import PluginPackages
from .deps import get_runtime

router = APIRouter()
_mutation = asyncio.Lock()


def packages():
    return PluginPackages(CapabilityStore(get_runtime().store._connect),
        config._PROJECT_ROOT / 'data/private/plugin-packages', config._PROJECT_ROOT / 'plugins')


class ImportPackage(BaseModel):
    path: str


class PackageAction(BaseModel):
    action: str
    revision: str | None = None
    trusted: bool = False


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
