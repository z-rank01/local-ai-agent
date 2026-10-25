"""Immutable imported package content and pending versions in the original DB."""
import json
from pathlib import Path
import re
import tempfile

from harness.contracts.plugin_manifest import read_manifest
from harness.infrastructure.plugin_archive import read_package, content_revision


class PluginPackages:
    def __init__(self, store, storage: Path, bundled: Path):
        self.store, self.storage, self.bundled = store, storage, bundled

    def import_source(self, source: Path):
        files = read_package(source)
        metadata = json.loads(files['plugin.json'])
        identity = metadata.get('id') if isinstance(metadata, dict) else None
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', identity):
            raise ValueError('插件标识无效')
        if (self.bundled / identity).exists():
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
            destination = self.storage / revision / identity
            if destination.exists():
                if content_revision(read_package(destination)) != revision:
                    raise ValueError('已保存的插件版本内容校验失败')
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                package.rename(destination)
        states = self.store.list('__host__', 'plugin_package')
        state = states.get(identity, {'active': None, 'history': []})
        previous = state.get('pending')
        if previous and previous['revision'] != revision and all(
                row['revision'] != previous['revision'] for row in state['history']):
            state['history'].append(previous)
        entry = {'revision': revision, 'manifest': metadata, 'source': str(source.absolute())}
        state.update(pending=entry, uninstalled=False)
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
