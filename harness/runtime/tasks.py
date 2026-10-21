"""Namespace routing for installed task providers; no domain-specific prefixes."""
import importlib
import json


class TaskRegistry:
    def __init__(self):
        self.providers = {}
        self.errors = {}

    def register(self, provider):
        namespace = provider.namespace
        if not isinstance(namespace, str) or not namespace or ':' in namespace or namespace in self.providers:
            raise ValueError('任务提供者命名空间无效或冲突')
        self.providers[namespace] = provider

    def for_run(self, identity):
        namespace, separator, _ = identity.partition(':')
        if not separator:
            return None
        if namespace not in self.providers:
            raise KeyError('任务提供者未安装：' + namespace)
        return self.providers[namespace]

    @classmethod
    def bundled(cls, packages):
        registry = cls()
        for manifest in sorted(packages.glob('*/plugin.json')):
            try:
                if not manifest.resolve().is_relative_to(packages.resolve()):
                    raise ValueError('任务插件清单越界')
                metadata = json.loads(manifest.read_text(encoding='utf-8'))
                contribution = metadata.get('task_provider')
                if not contribution:
                    continue
                identity = manifest.parent.name
                if metadata.get('id') != identity or metadata.get('harness_api') != 1:
                    raise ValueError('插件契约版本或标识无效')
                module, name = contribution.split(':')
                if not module.startswith('plugins.' + identity + '.'):
                    raise ValueError('任务提供者必须属于自身插件')
                registry.register(getattr(importlib.import_module(module), name)())
            except Exception as exc:
                registry.errors[manifest.parent.name] = str(exc)
        return registry
