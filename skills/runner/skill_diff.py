"""Bounded, non-executing review of two validated package revisions."""
from difflib import unified_diff
from pathlib import Path
from skill_versions import _contents


def compare_versions(store, name, before, after):
    old = _contents(Path(store.read(name, before).path)) if before else {}
    new = _contents(Path(store.read(name, after).path))
    changes = []
    remaining = 64000
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path), new.get(path)
        if left == right:
            continue
        row = {'path': path, 'change': 'added' if left is None else 'removed' if right is None else 'modified',
               'before_bytes': len(left or b''), 'after_bytes': len(right or b'')}
        try:
            # Huge resources still have a file-level receipt; never materialize an unbounded diff.
            if max(len(left or b''), len(right or b'')) > 128000 or remaining <= 0:
                row['preview'] = 'too_large'
            else:
                a, b = (left or b'').decode('utf-8'), (right or b'').decode('utf-8')
                if '\x00' in a or '\x00' in b:
                    raise UnicodeError()
                diff = ''.join(unified_diff(a.splitlines(True), b.splitlines(True),
                                           fromfile='before/' + path, tofile='after/' + path))
                row.update(diff=diff[:remaining], truncated=len(diff) > remaining)
                remaining -= len(row['diff'])
        except UnicodeError:
            row['preview'] = 'binary'
        changes.append(row)
    return {'name': name, 'before': before, 'after': after, 'files': changes}
