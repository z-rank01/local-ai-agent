"""Run-bound management of host packages with explicit workspace delegation."""
import asyncio
import json
from core import config
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.package_authority import valid_identity, verify_package
from harness.infrastructure.plugin_archive import content_revision, read_package
from harness.infrastructure.plugin_packages import PluginPackages, mutation_lock
from harness.capabilities.catalog import package_catalog
from harness.capabilities.skill_catalog import SkillCatalog


class PackageManagement:
    def __init__(self, runtime, context=None):
        self.runtime = runtime
        self.context = context

    def bind(self, context):
        return type(self)(self.runtime,context)

    async def call_tool(self, tool, arguments, session_id):
        event = {'actor': session_id, 'run_id': getattr(self.context, 'run_id', ''),
                 'workspace': getattr(self.context, 'workspace_id', ''),
                 'scope': 'workspace' if arguments.get('kind') == 'skill' else 'host',
                 'identity': arguments.get('identity'), 'action': arguments.get('action')}
        audit = getattr(self.runtime, 'audit', None)
        try:
            result = await self._apply(arguments)
        except Exception as exc:
            if audit:
                audit.record('capability_management', {**event, 'error': str(exc)})
            raise
        if audit:
            audit.record('capability_management', {**event,
                'revision': result.get('revision') or result.get('target'),
                'status': result.get('deployment') or result.get('status') or result.get('state')})
        return result

    async def _apply(self, arguments):
        ctx = self.context
        if ctx is None or 'workspace.write' not in ctx.permissions:
            raise PermissionError('能力管理需要本回合的工作区写授权')
        identity = arguments['identity']
        if not valid_identity(identity, arguments.get('kind', 'plugin')):
            raise ValueError('插件标识无效')
        store = CapabilityStore(self.runtime.store._connect)
        action = arguments['action']
        async with mutation_lock:
            if arguments.get('kind') == 'skill':
                return await self._skill(store, identity, action, arguments)
            grant = store.list(ctx.workspace_id,'host_package_grant').get(identity)
            if not grant or action not in grant['actions']:
                raise PermissionError('该工作区尚无此宿主插件操作授权')
            packages = PluginPackages(store,config._PROJECT_ROOT/'data/private/plugin-packages',
                                      config._PROJECT_ROOT/'plugins')
            state = store.list('__host__','plugin_package').get(identity,{})
            if action == 'status':
                return {'id':identity,'deployment':state.get('deployment'),'active':state.get('deployed_revision')}
            if action in ('import','install','upgrade'):
                installed = bool(state.get('deployed_revision') or state.get('target'))
                if action == 'install' and installed:
                    raise PermissionError('替换现有插件版本需要升级授权')
                if action == 'upgrade' and not installed:
                    raise PermissionError('首次部署插件需要安装授权')
                source = ctx.resolve_path(arguments['path'])
                files = await asyncio.to_thread(read_package,source)
                manifest = json.loads(files['plugin.json'])
                if manifest.get('id') != identity:
                    raise ValueError('包身份与授权不匹配')
                revision = content_revision(files)
                await asyncio.to_thread(verify_package,grant,identity,revision,
                                        arguments.get('signature'),manifest.get('permissions',[]))
                if action == 'upgrade':
                    previous_revision = state.get('target') or state.get('deployed_revision')
                    previous = packages.read(identity, previous_revision) / 'plugin.json'
                    previous_permissions = json.loads(previous.read_text('utf8')).get('permissions', [])
                    self._check_upgrade(grant, revision, previous_permissions, manifest.get('permissions', []))
                await asyncio.to_thread(packages.import_files,files,source)
                if action=='import':
                    return {'id':identity,'revision':revision,'status':'IMPORTED'}
                state = await asyncio.to_thread(packages.queue,identity,'activate',revision,trusted=True)
            elif action=='uninstall':
                state = await asyncio.to_thread(packages.queue,identity,'uninstall')
            elif action in ('enable','disable'):
                from harness.capabilities.enabling import set_managed_enabled
                if not state.get('deployed_revision') or state.get('deployment')=='BUILD_REQUIRED':
                    raise ValueError('当前包没有可启停的已部署版本')
                if action=='enable':
                    revision = state['deployed_revision']
                    path = packages.read(identity,revision)
                    manifest = json.loads((path/'plugin.json').read_text('utf8'))
                    await asyncio.to_thread(verify_package,grant,identity,revision,
                                            arguments.get('signature'),manifest.get('permissions',[]))
                return await set_managed_enabled(self.runtime,identity,action=='enable')
            else:
                raise ValueError('未知插件管理操作')
            return {'id':identity,'deployment':state['deployment'],'target':state.get('target')}

    async def _skill(self, store, name, action, arguments):
        ctx = self.context
        grant = store.list(ctx.workspace_id, 'skill_source_grant').get(name)
        if not grant or action not in grant['actions']:
            raise PermissionError('该工作区尚无此技能操作授权')
        state = store.list(ctx.workspace_id).get(name, {})
        if action == 'status':
            return {'id': name, 'enabled': state.get('enabled', False),
                    'revision': (state.get('active') or {}).get('revision')}
        if ctx.runner_operation is None:
            raise ValueError('当前工作区未绑定技能版本服务')
        registry = self.runtime.tool_registry
        capabilities = package_catalog(config._PROJECT_ROOT / 'plugins', registry.ownership())
        manager = self.runtime.capabilities
        available = [row['id'] for row in capabilities if row['registration'] == 'registered'
                     and all(not manager or not manager.manages(owner) or manager.status(owner)['accepting']
                             for owner in row.get('owners', []))]
        catalog = SkillCatalog(store, ctx.workspace_id, available, ctx.permissions,
                               hidden_packages=registry.hidden_research_methods())
        if state and not state.get('signed_import'):
            raise PermissionError('不能覆盖工作区已有的用户技能')
        if action in ('disable', 'uninstall'):
            return catalog.action(name, action)
        if action == 'enable':
            entry = state.get('active')
            if not entry:
                raise ValueError('没有可启用的技能版本')
            await ctx.runner_operation('read', {'name': name, 'revision': entry['revision']})
        else:
            if action not in ('import', 'install', 'upgrade'):
                raise ValueError('未知技能管理操作')
            installed = bool(state.get('active')) and not state.get('uninstalled')
            if (action == 'install' and installed) or (action == 'upgrade' and not installed):
                raise PermissionError('须按实际安装状态使用对应授权')
            snapshot = await ctx.runner_operation('freeze', {})
            if name in snapshot['versions'] or any(row.get('name') == name for row in snapshot.get('invalid', [])):
                raise ValueError('工作区已有同名技能文件')
            ctx.resolve_path(arguments['path'])
            entry = await ctx.runner_operation('capture-import', {'path': arguments['path'], 'name': name})
        await asyncio.to_thread(verify_package, grant, 'skill/' + name, entry['revision'],
                                arguments.get('signature'), entry.get('permissions', []))
        if action == 'upgrade':
            self._check_upgrade(grant, entry['revision'], state['active'].get('permissions', []),
                                entry.get('permissions', []))
        blockers = catalog.blockers(entry)
        if action != 'import' and blockers:
            raise ValueError('；'.join(blockers))
        if action == 'enable':
            return catalog.action(name, action)
        state = {**{'active': None, 'history': [], 'enabled': False}, **state,
                 'signed_import': True, 'follow_updates': False, 'missing': False, 'pending': entry}
        if action != 'import':
            catalog._activate(state, entry)
            state.update(enabled=True, uninstalled=False)
        store.save(ctx.workspace_id, name, state)
        return {'id': name, 'revision': entry['revision'],
                'status': 'IMPORTED' if action == 'import' else 'ACTIVE_FOR_NEW_RUNS'}

    @staticmethod
    def _check_upgrade(grant, revision, previous, requested):
        if set(requested) - set(previous) and grant.get('approved_revision') != revision:
            raise PermissionError('升级增加权限，请在受信来源管理中确认具体版本：' + revision)
