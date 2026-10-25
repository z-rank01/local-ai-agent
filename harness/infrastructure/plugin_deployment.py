"""Apply reviewed package versions only during the unique launcher's cold start."""
from pathlib import Path
import shutil
import tempfile

from .plugin_archive import read_package, content_revision


def apply_pending(packages):
    changed = []
    for identity, state in packages.store.list('__host__', 'plugin_package').items():
        if state.get('deployment') != 'PENDING_RESTART':
            continue
        try:
            destination = packages.bundled / identity
            if destination.parent.resolve() != packages.bundled.resolve() or destination.is_symlink():
                raise ValueError('插件部署目录越界')
            removing = state.get('remove_requested', False)
            revision = state.get('target')
            if not removing and state.get('trusted_revision') != revision:
                raise ValueError('部署版本未得到信任确认')
            source = None if removing else packages.read(identity, revision)
            current = content_revision(read_package(destination)) if destination.exists() else None
            allowed = {state.get('deployed_revision')}
            if not removing:
                allowed.add(revision)  # Replay a known local copy interrupted before its DB receipt.
            if current is not None and current not in allowed:
                raise ValueError('部署目录已被修改，拒绝覆盖；请保留并检查现有内容')
            packages.bundled.mkdir(parents=True, exist_ok=True)
            # Both rename targets are verified children of the controlled plugin root.
            with tempfile.TemporaryDirectory(prefix='.deployment-', dir=packages.bundled) as temporary:
                staging = Path(temporary)
                replacement, backup = staging / identity, staging / '.previous'
                if source:
                    shutil.copytree(source, replacement)
                    if content_revision(read_package(replacement)) != revision:
                        raise ValueError('部署复制内容校验失败')
                if destination.exists():
                    destination.rename(backup)
                try:
                    if source:
                        replacement.rename(destination)
                except BaseException:
                    if backup.exists():
                        backup.rename(destination)
                    raise
            previous = state.get('active')
            if previous and all(row['revision'] != previous['revision'] for row in state['history']):
                state['history'].append(previous)
            entries = [state.get('pending'), previous, *state['history']]
            active = next((row for row in entries if row and row['revision'] == revision), None)
            state.update(active=None if removing else active, deployed_revision=None if removing else revision,
                         uninstalled=removing, deployment='BUILD_REQUIRED', error='')
            if state.get('pending') == active:
                state['pending'] = None
            # A deployed package remains disabled until its environment is verified and explicitly enabled.
            packages.store.save('__host__', identity, {'enabled': False}, 'plugin')
            changed.append(identity)
        except Exception as exc:
            state.update(deployment='DEPLOY_FAILED', error=str(exc))
        packages.store.save('__host__', identity, state, 'plugin_package')
    states = packages.store.list('__host__', 'plugin_package')
    return {'changed': changed, 'build_required': any(row.get('deployment') == 'BUILD_REQUIRED' for row in states.values()),
            'errors': {identity: row['error'] for identity, row in states.items() if row.get('deployment') == 'DEPLOY_FAILED'}}


def confirm_build(packages):
    for identity, state in packages.store.list('__host__', 'plugin_package').items():
        if state.get('deployment') == 'BUILD_REQUIRED':
            state.update(deployment='REMOVED' if state.get('uninstalled') else 'INSTALLED', target=None)
            packages.store.save('__host__', identity, state, 'plugin_package')
