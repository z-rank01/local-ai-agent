"""Bounded review of already registered package versions."""
import difflib
from .plugin_archive import read_package


def package_diff(packages, identity, after):
    state = packages.store.list('__host__', 'plugin_package').get(identity, {})
    active = state.get('active')
    before_files = read_package(packages.read(identity, active['revision'])) if active else {}
    after_files = read_package(packages.read(identity, after))
    rows = []
    budget = 128 * 1024
    for name in sorted(before_files.keys() | after_files.keys()):
        old, new = before_files.get(name), after_files.get(name)
        if old == new:
            continue
        row = {'path': name, 'change': 'added' if old is None else 'removed' if new is None else 'changed',
               'before_bytes': len(old or b''), 'after_bytes': len(new or b'')}
        if max(row['before_bytes'], row['after_bytes']) <= 64 * 1024 and budget > 0:
            try:
                a, b = (old or b'').decode('utf-8'), (new or b'').decode('utf-8')
                difference = ''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                    fromfile='before/' + name, tofile='after/' + name))
                row.update(diff=difference[:min(budget, 12000)], truncated=len(difference) > min(budget, 12000))
                budget -= len(row['diff'])
            except UnicodeDecodeError:
                row['binary'] = True
        else:
            row['truncated'] = True
        rows.append(row)
    return {'before': active['revision'] if active else None, 'after': after, 'files': rows}
