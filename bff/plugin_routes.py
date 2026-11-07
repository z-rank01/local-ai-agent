"""Mount HTTP contributions from installed plugins, after host routes exist."""
from importlib import import_module

from fastapi import APIRouter
from harness.contracts.plugin_manifest import read_manifest


def mount_plugin_routes(app, packages):
    for file in sorted(packages.glob('*/plugin.json')):
        manifest = read_manifest(file,packages)
        reference = manifest.get('http_router')
        if reference is None:
            continue
        if manifest.get('tool_registration') == 'managed':
            raise ValueError('托管工具包不能在宿主进程注册 Python 路由')
        module, _, name = reference.partition(':')
        if not module.startswith('plugins.'+manifest['id']+'.') or not name.isidentifier():
            raise ValueError('HTTP 路由贡献必须属于当前插件')
        router = getattr(import_module(module),name)
        if not isinstance(router,APIRouter):
            raise ValueError('HTTP 路由贡献必须是 APIRouter')
        app.include_router(router)
