"""Provider registration for saved artifacts; no domain-specific tool names."""
import importlib
import json


class ArtifactRegistry:
    def __init__(self):
        self.providers, self.errors = {}, {}

    def register(self, tools, provider):
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or not t for t in tools):
            raise ValueError('成果提供者必须声明来源工具')
        if len(set(tools)) != len(tools) or any(t in self.providers for t in tools):
            raise ValueError('成果提供者登记冲突')
        self.providers.update(dict.fromkeys(tools, provider))

    @classmethod
    def bundled(cls, packages, transport_for):
        registry = cls()
        for manifest in sorted(packages.glob('*/plugin.json')):
            try:
                if not manifest.resolve().is_relative_to(packages.resolve()):
                    raise ValueError('成果插件清单越界')
                metadata = json.loads(manifest.read_text(encoding='utf-8'))
                declaration = metadata.get('artifacts')
                if not declaration:
                    continue
                identity = manifest.parent.name
                if metadata.get('id') != identity or metadata.get('harness_api') != 1:
                    raise ValueError('成果插件契约无效')
                module, name = declaration['provider'].split(':')
                owner = declaration['owner']
                if not module.startswith('plugins.' + identity + '.') or owner not in metadata.get('backends', []):
                    raise ValueError('成果提供者必须属于自身插件及已声明传输')
                provider = getattr(importlib.import_module(module), name)(lambda owner=owner: transport_for(owner))
                registry.register(declaration['tools'], provider)
            except Exception as exc:
                registry.errors[manifest.parent.name] = str(exc)
        return registry
