"""Load installed contributions; failures affect only their declared owners."""
import importlib
from hashlib import sha256
from harness.contracts.plugin_manifest import read_manifest


class CallPolicies:
    def __init__(self, packages=None):
        self.providers, self.errors, self.roles, self.limits = {}, {}, {}, {}
        self.turn_providers, self.plugin_owners = {}, {}
        self.groups = {}
        self.reply_purposes = {}
        self.research_session = None
        self.method_families = {}
        if packages is None:
            return
        for manifest in sorted(packages.glob('*/plugin.json')):
            owners = []
            try:
                metadata = read_manifest(manifest, packages)
                owners = metadata.get('owners', metadata['backends'])
                self.plugin_owners[metadata['id']] = tuple(owners)
                for owner in owners:
                    self.roles[owner] = metadata.get('tool_roles', {})
                    self.limits[owner] = metadata.get('call_limits', {})
                    self.groups[owner] = metadata.get('ui', {}).get('group', metadata['title'])
                    self.reply_purposes[owner] = metadata.get('reply_purposes', {})
                self.method_families[metadata['id']] = metadata.get('research_methods', [])
                if metadata.get('research_session'):
                    module, name = metadata['research_session'].split(':')
                    if not module.startswith('plugins.' + metadata['id'] + '.') or not name.isidentifier():
                        raise ValueError('研究会话入口必须属于当前插件')
                    self.research_session = getattr(importlib.import_module(module), name)
                turn_reference = metadata.get('turn_policy')
                if turn_reference:
                    module, name = turn_reference.split(':')
                    if not module.startswith('plugins.' + metadata['id'] + '.') or not name.isidentifier():
                        raise ValueError('回合策略入口必须属于当前插件')
                    self.turn_providers[metadata['id']] = getattr(importlib.import_module(module), name)
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

    def create_turn(self, active_owners, context=None):
        from harness.runtime.turns import NullTurn
        identities = [identity for identity in self.turn_providers
                      if any(owner in active_owners for owner in self.plugin_owners[identity])]
        if len(identities) > 1:
            raise ValueError('当前回合存在多个冲突的业务编排者：' + ', '.join(identities))
        return self.turn_providers[identities[0]](context) if identities else NullTurn()

    def research_methods(self, active_owners):
        return [family for identity, families in self.method_families.items()
                if any(owner in active_owners for owner in self.plugin_owners[identity])
                for family in families]

    def create_research(self, router, **kwargs):
        if self.research_session is None:
            raise ValueError('研究能力未登记')
        active_owners = kwargs.pop('active_owners', ())
        kwargs.setdefault('method_families', self.research_methods(active_owners))
        return self.research_session(router=router, **kwargs)

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
        if metadata.get('review_prompt'):
            source = (manifest.parent / metadata['review_prompt']).resolve()
            if not source.is_relative_to(manifest.parent.resolve()) or not source.is_file():
                raise ValueError('独立核对提示必须属于当前插件')
            digest.update(metadata['review_prompt'].encode('utf-8'))
            digest.update(source.read_bytes())
        versions[metadata['id']] = digest.hexdigest()
    return versions
