"""Trusted bundled container packages use the same lifecycle and public registry."""
import importlib
import json
import yaml
from core.workspaces import WorkspaceManager
from core import config
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.container_client import ContainerClient


class ContainerPackage:
    def __init__(self, root, metadata, runtime):
        self.root, self.metadata, self.runtime = root, metadata, runtime
        self.client = ContainerClient(root.parents[1], WorkspaceManager(runtime.store, config.WORKSPACE_PATH),
                                      metadata['id'], metadata['service'])

    async def activate(self):
        await self.client.prepared()
        identity = self.metadata['id']
        module, name = self.metadata['transport'].split(':')
        if not module.startswith('plugins.' + identity + '.'):
            raise ValueError('插件传输适配器必须属于自身包')
        provider = getattr(importlib.import_module(module), name)(self.client)
        definitions = [yaml.safe_load(path.read_text(encoding='utf-8')) for path in sorted((self.root / 'tools').glob('*.yaml'))]
        if any(tool.get('backend') != identity for tool in definitions):
            raise ValueError('容器插件不能登记其他插件的传输后端')
        self.runtime.tool_registry.replace_owner(identity, definitions)
        self.runtime.router.bind_provider(identity, provider)

    async def deactivate(self):
        await self.client.stop()
        self.runtime.tool_registry.unregister_owner(self.metadata['id'])
        self.runtime.router.bind_provider(self.metadata['id'], None)


async def restore_containers(runtime, packages):
    runtime.container_packages = {}
    states = CapabilityStore(runtime.store._connect).list('__host__', 'plugin')
    store = CapabilityStore(runtime.store._connect)
    for identity, state in store.list('__host__', 'plugin_environment').items():
        if state.get('state') == 'PREPARING':
            store.save('__host__', identity, {'state': 'INTERRUPTED',
                'error': '宿主在准备完成确认前重启，请检查环境后重新准备'}, 'plugin_environment')
    for manifest in sorted(packages.glob('*/plugin.json')):
        try:
            if not manifest.resolve().is_relative_to(packages.resolve()):
                raise ValueError('插件清单越界')
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            if metadata.get('execution') != 'container':
                continue
            identity = metadata['id']
            if identity != manifest.parent.name or metadata.get('harness_api') != 1:
                continue
            package = ContainerPackage(manifest.parent, metadata, runtime)
            runtime.capabilities.register(identity, package, required=metadata.get('required', []))
        except (OSError, ValueError, TypeError, KeyError):
            # Invalid declarations are already reported by the read-only catalog.
            continue
        runtime.container_packages[identity] = package
        if states.get(identity, {}).get('enabled'):
            try:
                await runtime.capabilities.activate(identity)
            except Exception:
                pass  # Lifecycle retains the exact error without blocking chat.
