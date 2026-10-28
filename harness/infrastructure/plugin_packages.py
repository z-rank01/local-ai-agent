"""Immutable imported package content and pending versions in the original DB."""
import json
from pathlib import Path
import re
import tempfile
import time

from harness.contracts.plugin_manifest import read_manifest
from harness.contracts.plugin_payload import validate_payload
from harness.infrastructure.plugin_archive import read_package, content_revision


def rename_with_brief_hold_retry(package: Path, destination: Path):
    # Windows AV/indexer may hold a freshly extracted tree for a moment; the
    # staging TemporaryDirectory is still open, so retry instead of failing.
    for attempt in range(5):
        try:
            package.rename(destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


class PluginPackages:
    def __init__(self, store, storage: Path, bundled: Path):
        self.store, self.storage, self.bundled = store, storage, bundled

    def import_source(self, source: Path):
        files = read_package(source)
        metadata = json.loads(files['plugin.json'])
        identity = metadata.get('id') if isinstance(metadata, dict) else None
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', identity):
            raise ValueError('插件标识无效')
        installed = self.store.list('__host__', 'plugin_package').get(identity, {})
        if (self.bundled / identity).exists() and not installed.get('deployed_revision'):
            raise ValueError('导入包不能覆盖随宿主安装的插件')
        revision = content_revision(files)
        self.storage.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='import-', dir=self.storage) as staging:
            package = Path(staging) / identity
            for name, data in files.items():
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            metadata = read_manifest(package / 'plugin.json', Path(staging))
            if metadata.get('tool_registration') != 'managed':
                raise ValueError('外部插件必须使用托管登记契约')
            validate_payload(package, metadata)
            destination = self.storage / revision / identity
            if destination.exists():
                if content_revision(read_package(destination)) != revision:
                    raise ValueError('已保存的插件版本内容校验失败')
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                rename_with_brief_hold_retry(package, destination)
        states = self.store.list('__host__', 'plugin_package')
        state = states.get(identity, {'active': None, 'history': []})
        previous = state.get('pending')
        if previous and previous['revision'] != revision and all(
                row['revision'] != previous['revision'] for row in state['history']):
            state['history'].append(previous)
        entry = {'revision': revision, 'manifest': metadata, 'source': str(source.absolute())}
        unchanged = (state.get('active') or {}).get('revision') == revision
        state.update(pending=None if unchanged else entry, uninstalled=False)
        self.store.save('__host__', identity, state, 'plugin_package')
        return {'id': identity, **state}

    def read(self, identity, revision):
        if (not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', identity)
                or not re.fullmatch(r'[a-f0-9]{64}', revision)):
            raise ValueError('插件版本标识无效')
        state = self.store.list('__host__', 'plugin_package').get(identity, {})
        candidates = [state.get('pending'), state.get('active'), *state.get('history', [])]
        if not any(row and row['revision'] == revision for row in candidates):
            raise ValueError('插件版本未登记')
        path = self.storage / revision / identity
        if content_revision(read_package(path)) != revision:
            raise ValueError('插件内容与已登记版本不一致')
        return path

    def queue(self, identity, action, revision=None, *, trusted=False):
        state = self.store.list('__host__', 'plugin_package').get(identity)
        if state is None:
            raise ValueError('插件未导入')
        if action in ('activate', 'rollback'):
            if not trusted:
                raise ValueError('须确认信任此具体版本及其声明权限')
            self.read(identity, revision or '')
            state.update(target=revision, trusted_revision=revision, remove_requested=False,
                         deployment='PENDING_RESTART', error='')
        elif action == 'uninstall':
            state.update(target=None, remove_requested=True, deployment='PENDING_RESTART', error='')
        else:
            raise ValueError('不支持的插件安装操作')
        self.store.save('__host__', identity, state, 'plugin_package')
        return {'id': identity, **state}
