"""Read-only package inventory. A manifest never imports or activates package code."""
from dataclasses import asdict
import json
from pathlib import Path
import re
from harness.contracts import CapabilityDescriptor


def package_catalog(root: Path, ownership: dict[str, str]) -> list[dict]:
    rows = []
    for manifest in sorted(root.glob('*/plugin.json')):
        package_id = manifest.parent.name
        try:
            if not manifest.resolve().is_relative_to(root.resolve()):
                raise ValueError('清单不能通过链接指向插件目录外')
            value = json.loads(manifest.read_text(encoding='utf-8'))
            if value.get('id') != package_id or not re.fullmatch(r'[a-z][a-z0-9-]*', package_id):
                raise ValueError('清单 ID 必须与目录一致')
            if type(value.get('harness_api')) is not int or value['harness_api'] != 1:
                raise ValueError('不支持的 Harness API 版本')
            for field in ('version', 'title', 'description'):
                if not isinstance(value.get(field), str) or not value[field].strip():
                    raise ValueError(f'缺少清单字段：{field}')
            for field in ('backends', 'required', 'optional', 'permissions'):
                if not isinstance(value.get(field), list) or any(not isinstance(x, str) or not x for x in value[field]):
                    raise ValueError(f'无效清单字段：{field}')
            descriptor = CapabilityDescriptor(package_id, value['version'], 'plugin', value['title'],
                value['description'], tuple(value['required']), tuple(value['optional']), tuple(value['permissions']))
            owners = value.get('owners', value['backends'])
            tools = sorted(name for name, owner in ownership.items() if owner in owners)
            rows.append({**asdict(descriptor), 'api_version': 1, 'tools': tools,
                'backends': value['backends'], 'owners': owners,
                'registration': 'registered' if tools else 'unregistered',
                'management': 'managed' if value.get('tool_registration') == 'managed' else 'legacy_adapter', 'error': None})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            rows.append({'id': package_id, 'kind': 'plugin', 'registration': 'invalid',
                'error': str(exc) if isinstance(exc, ValueError) else '清单不可读或格式无效', 'tools': []})
    return rows
