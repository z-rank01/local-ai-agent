"""Freeze public implementation inputs; never copy credentials or live account data."""
from pathlib import Path
import hashlib
import json
import argparse


def freeze(stock, output, inventory):
    chat = Path(__file__).resolve().parents[1]
    roots = {'chat': chat, 'stock': Path(stock).resolve()}
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Baseline already exists; do not overwrite it')
    result = {'schema': 1, 'purpose': 'quality-first-20261010', 'repositories': {}, 'files': {}}
    for name, root in roots.items():
        result['repositories'][name] = inventory[name]['head']
        for rel in inventory[name]['files']:
            p = (root / rel).resolve()
            if not p.is_relative_to(root.resolve()) or Path(rel).is_absolute() or '..' in Path(rel).parts:
                raise ValueError('Inventory path leaves repository')
            if not p.is_file() or p.suffix not in ('.py', '.md', '.yaml', '.yml', '.json', '.txt'):
                continue
            if not rel.startswith(('services/', 'core/', 'config/', '04_', 'tests/')):
                continue
            raw = p.read_bytes()
            target = output / name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            result['files'][name + '/' + rel] = hashlib.sha256(raw).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'repositories': result['repositories'], 'files': len(result['files'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stock', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--inventory', required=True)
    args = parser.parse_args()
    freeze(args.stock, args.output, json.loads(Path(args.inventory).read_text(encoding='utf-8-sig')))
