"""Manage explicitly activated tools packaged into the isolated file service."""
import httpx
import yaml
from harness.contracts.plugin_manifest import read_manifest
from harness.runtime.lifecycle import LifecycleManager
from harness.contracts.package_revision import package_revision


class PackagedProvider:
    def __init__(self, root, metadata, registry, endpoint, headers):
        self.root, self.metadata, self.registry = root, metadata, registry
        self.endpoint, self.headers = endpoint, headers

    async def activate(self):
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(self.endpoint + '/capabilities/plugins', headers=self.headers)
            response.raise_for_status()
        identity = self.metadata['id']
        if (response.json().get('versions', {}).get(identity) != self.metadata['version']
                or response.json().get('revisions', {}).get(identity) != package_revision(self.root)):
            raise ValueError('插件资源未部署或版本不一致，请通过唯一入口重建工具镜像')
        definitions = [yaml.safe_load(path.read_text(encoding='utf-8'))
                       for path in sorted((self.root / 'tools').glob('*.yaml'))]
        if any(tool.get('backend') != 'skill-files' for tool in definitions):
            raise ValueError('文件执行环境插件不能登记其他传输后端')
        self.registry.replace_owner(identity, definitions)

    async def deactivate(self):
        self.registry.unregister_owner(self.metadata['id'])


def register_packages(runtime, packages, endpoint, headers):
    if runtime.capabilities is None:
        runtime.capabilities = LifecycleManager()
    runtime.router.capabilities = runtime.capabilities
    for manifest in sorted(packages.glob('*/plugin.json')):
        try:
            metadata = read_manifest(manifest, packages)
            if metadata.get('tool_registration') != 'managed' or metadata.get('execution') != 'files':
                continue
            identity = metadata['id']
            runtime.capabilities.register(identity, PackagedProvider(manifest.parent, metadata,
                runtime.tool_registry, endpoint, headers), required=metadata.get('required', []))
        except (OSError, ValueError, KeyError):
            continue
