"""Load installed contributions; failures affect only their declared owners."""
import importlib
from hashlib import sha256
from harness.contracts.plugin_manifest import read_manifest


class CallPolicies:
    def __init__(self, packages=None):
        self.providers, self.errors, self.roles = {}, {}, {}
        if packages is None:
            return
        for manifest in sorted(packages.glob('*/plugin.json')):
            owners = []
            try:
                metadata = read_manifest(manifest, packages)
                owners = metadata.get('owners', metadata['backends'])
                for owner in owners:
                    self.roles[owner] = metadata.get('tool_roles', {})
                reference = metadata.get('call_policy')
                if not reference:
                    continue
                module, name = reference.split(':')
                if not module.startswith('plugins.' + metadata['id'] + '.') or not name.isidentifier():
                    raise ValueError('调用策略入口必须属于当前插件')
                if any(owner in self.providers or owner in self.errors for owner in owners):
                    raise ValueError('调用策略所有者重复')
                provider = getattr(importlib.import_module(module), name)()
                if not callable(getattr(provider, 'validate', None)) or not callable(getattr(provider, 'ends_window', None)):
                    raise ValueError('调用策略未实现公开契约')
                self.providers.update(dict.fromkeys(owners, provider))
            except Exception as exc:
                self.errors.update(dict.fromkeys(owners, str(exc)))

    def for_owner(self, owner):
        if owner in self.errors:
            raise ValueError('能力调用策略不可用：' + self.errors[owner])
        return self.providers.get(owner)


def contribution_versions(packages):
    """Pin manifest declarations and package Python sources for explicit continuation."""
    versions = {}
    for manifest in sorted(packages.glob('*/plugin.json')):
        try:
            metadata = read_manifest(manifest, packages)
        except (OSError, ValueError):
            continue  # Invalid packages are already excluded from registration.
        if not (metadata.get('call_policy') or metadata.get('tool_roles')):
            continue
        digest = sha256(manifest.read_bytes())
        for source in sorted(manifest.parent.rglob('*.py')):
            digest.update(source.relative_to(manifest.parent).as_posix().encode('utf-8'))
            digest.update(source.read_bytes())
        versions[metadata['id']] = digest.hexdigest()
    return versions
