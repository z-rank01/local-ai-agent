"""Skill revision activation. Descriptors are data, never an authority grant."""
from copy import deepcopy


class SkillCatalog:
    def __init__(self, store, workspace_id, available=(), permissions=()):
        self.store, self.workspace_id = store, workspace_id
        self.available, self.permissions = set(available), set(permissions)

    def blockers(self, entry):
        return ([f'缺少依赖：{name}' for name in entry.get('dependencies', []) if name not in self.available]
                + [f'宿主未授权：{name}' for name in entry.get('permissions', []) if name not in self.permissions])

    def reconcile(self, snapshot):
        states = self.store.list(self.workspace_id)
        for name, entry in snapshot['versions'].items():
            state = deepcopy(states.get(name, {'enabled': True, 'active': None, 'history': [], 'follow_updates': True}))
            state['missing'] = False
            active = state['active']
            if not active or active['revision'] != entry['revision']:
                prior = active or {}
                expanded = any(set(entry.get(key, [])) - set(prior.get(key, [])) for key in ('permissions', 'dependencies'))
                if state['follow_updates'] and not expanded and not self.blockers(entry):
                    self._activate(state, entry)
                else:
                    state['pending'] = entry
            else:
                state['pending'] = None
            states[name] = state
            self.store.save(self.workspace_id, name, state)
        for name, state in states.items():
            if name not in snapshot['versions']:
                state['missing'] = True
                self.store.save(self.workspace_id, name, state)
        return states

    @staticmethod
    def _activate(state, entry):
        previous = state.get('active')
        if previous and all(row['revision'] != previous['revision'] for row in state['history']):
            state['history'].append(previous)
        state.update(active=deepcopy(entry), pending=None)

    def action(self, name, action, revision=None):
        state = deepcopy(self.store.list(self.workspace_id).get(name))
        if state is None:
            raise ValueError('技能未登记，请先刷新目录')
        if action in ('enable', 'disable', 'uninstall'):
            state['enabled'] = action == 'enable'
            state['uninstalled'] = action == 'uninstall'
        elif action in ('activate', 'rollback'):
            candidates = [state.get('pending'), state.get('active'), *state['history']]
            entry = next((row for row in candidates if row and row['revision'] == revision), None)
            if not entry:
                raise ValueError('版本不在此技能的已登记历史中')
            errors = self.blockers(entry)
            if errors:
                raise ValueError('；'.join(errors))
            self._activate(state, entry)
            state.update(follow_updates=action == 'activate', enabled=True, uninstalled=False)
        else:
            raise ValueError('不支持的技能操作')
        self.store.save(self.workspace_id, name, state)
        return state

    def effective(self, snapshot):
        states = self.reconcile(snapshot)
        result = {**snapshot, 'versions': {}, 'invalid': list(snapshot.get('invalid', []))}
        for name, state in states.items():
            entry = state.get('active')
            if not state['enabled'] or state.get('missing') or state.get('uninstalled') or not entry:
                continue
            errors = self.blockers(entry)
            if errors:
                result['invalid'].append({'name': name, 'kind': 'skill_md', 'valid': False, 'error': '；'.join(errors)})
            else:
                result['versions'][name] = entry
        return result
