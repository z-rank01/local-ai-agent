"""Read-only package inventory. A manifest never imports or activates package code."""
from dataclasses import asdict
from pathlib import Path
from harness.contracts import CapabilityDescriptor
from harness.contracts.plugin_manifest import read_manifest


def package_catalog(root: Path, ownership: dict[str, str]) -> list[dict]:
    rows = []
    for manifest in sorted(root.glob('*/plugin.json')):
        package_id = manifest.parent.name
        try:
            value = read_manifest(manifest, root)
            descriptor = CapabilityDescriptor(package_id, value['version'], 'plugin', value['title'],
                value['description'], tuple(value['required']), tuple(value['optional']), tuple(value['permissions']))
            owners = value.get('owners', value['backends'])
            tools = sorted(name for name, owner in ownership.items() if owner in owners)
            rows.append({**asdict(descriptor), 'api_version': 1, 'tools': tools,
                'backends': value['backends'], 'owners': owners,
                'environment_required': value.get('execution') == 'container',
                'registration': 'registered' if tools else 'unregistered',
                'management': 'managed' if value.get('tool_registration') == 'managed' else 'legacy_adapter', 'error': None})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            rows.append({'id': package_id, 'kind': 'plugin', 'registration': 'invalid',
                'error': str(exc) if isinstance(exc, ValueError) else '清单不可读或格式无效', 'tools': []})
    return rows
