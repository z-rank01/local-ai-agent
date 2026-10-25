"""Restore pre-build package content after an unsuccessful cold-start build."""
from .plugin_deployment import apply_pending


def restore_before_build(packages, error):
    restoring = {}
    for identity, state in packages.store.list('__host__', 'plugin_package').items():
        if state.get('deployment') != 'BUILD_REQUIRED':
            continue
        baseline = state.get('build_baseline')
        if baseline is None:
            raise ValueError('缺少构建前版本记录，不能猜测回退目标：' + identity)
        previous = baseline['active']
        restoring[identity] = (baseline, state.get('active'))
        revision = previous['revision'] if previous else None
        state.update(target=revision, trusted_revision=revision, remove_requested=previous is None,
                     deployment='PENDING_RESTART')
        packages.store.save('__host__', identity, state, 'plugin_package')
    result = apply_pending(packages)
    if result['errors']:
        raise ValueError('构建回退未完成：' + str(result['errors']))
    for identity, (baseline, candidate) in restoring.items():
        state = packages.store.list('__host__', 'plugin_package')[identity]
        state.update(deployment='BUILD_FAILED', error=str(error), target=None)
        if candidate and candidate != baseline['active']:
            state['pending'] = candidate
        state.pop('build_baseline', None)
        packages.store.save('__host__', identity, state, 'plugin_package')
        packages.store.save('__host__', identity, {'enabled': baseline['enabled']}, 'plugin')
    return {'restored': list(restoring)}
