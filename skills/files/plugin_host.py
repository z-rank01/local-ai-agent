"""Execute only image-packaged providers behind the existing workspace auth."""
import importlib.util
import json
from pathlib import Path
import threading
import yaml
from harness.contracts.tool_provider import ExecutionContext
from harness.contracts.package_revision import package_revision


class PluginHost:
    def __init__(self, packages, context):
        self.packages, self.context = Path(packages), context
        self._providers = {}
        self._lock = threading.Lock()
        self._tools = {}
        self.versions = {}
        self.revisions = {}
        self.errors = {}
        for manifest in sorted(self.packages.glob('*/plugin.json')):
            try:
                self._register(manifest)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
                self.errors[manifest.parent.name] = str(exc)

    def _register(self, manifest):
        metadata = json.loads(manifest.read_text(encoding='utf-8'))
        if metadata.get('execution') != 'files':
            return
        identity = metadata['id']
        if identity != manifest.parent.name or metadata.get('harness_api') != 1:
            raise ValueError('Plugin identity or API mismatch')
        package = manifest.parent.resolve()
        if not manifest.resolve().is_relative_to(self.packages.resolve()):
            raise ValueError('Plugin manifest escapes image package root')
        provider = package / 'runtime/provider.py'
        if not provider.is_file() or not provider.resolve().is_relative_to(package):
            raise ValueError('Plugin provider is missing or outside package')
        pending = {}
        for path in sorted((package / 'tools').glob('*.yaml')):
            if not path.resolve().is_relative_to(package):
                raise ValueError('Tool declaration escapes package')
            tool = yaml.safe_load(path.read_text(encoding='utf-8'))
            name = tool['name']
            if name in self._tools or name in pending:
                raise ValueError('Duplicate packaged tool: ' + name)
            pending[name] = identity
        version, revision = metadata['version'], package_revision(package)
        self._tools.update(pending)
        self.versions[identity] = version
        self.revisions[identity] = revision

    def invoke(self, tool, arguments):
        if tool not in self._tools:
            raise KeyError(tool)
        identity = self._tools[tool]
        with self._lock:
            if identity not in self._providers:
                path = self.packages / identity / 'runtime/provider.py'
                spec = importlib.util.spec_from_file_location('harness_plugin_' + identity.replace('-', '_'), path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._providers[identity] = module.Provider()
            provider = self._providers[identity]
        return provider.invoke(tool, arguments, self.context)
