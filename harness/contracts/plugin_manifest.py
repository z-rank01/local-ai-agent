"""Shared, data-only validation before a package can be displayed or registered."""
import json
import re
from pathlib import Path


def read_manifest(manifest: Path, packages: Path) -> dict:
    package = manifest.parent
    if not manifest.resolve().is_relative_to(packages.resolve()):
        raise ValueError('插件清单越界')
    raw = manifest.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError('插件清单超过大小限制')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('插件清单须为对象')
    identity = value.get('id')
    if identity != package.name or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', identity or ''):
        raise ValueError('清单 ID 必须与目录一致且为有效标识')
    if type(value.get('harness_api')) is not int or value['harness_api'] != 1:
        raise ValueError('不支持的 Harness API 版本')
    for field in ('version', 'title', 'description'):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f'缺少清单字段：{field}')
    for field in ('backends', 'required', 'optional', 'permissions', 'owners'):
        items = value.get(field, value.get('backends') if field == 'owners' else None)
        if (not isinstance(items, list) or any(not isinstance(x, str) or not x.strip() for x in items)
                or len(items) != len(set(items))):
            raise ValueError(f'无效清单字段：{field}')
    if identity in value['required'] or identity in value['optional']:
        raise ValueError('插件不能依赖自身')
    roles = value.get('tool_roles', {})
    if not isinstance(roles, dict) or any(
            not isinstance(role, str) or not isinstance(names, list)
            or any(not isinstance(name, str) or not name for name in names)
            for role, names in roles.items()):
        raise ValueError('工具角色声明须为角色到工具名称列表的映射')
    limits = value.get('call_limits', {})
    if (not isinstance(limits, dict) or any(
            not isinstance(name, str) or not name
            or not ((type(limit) is int and limit >= 0) or (isinstance(limit, str) and limit.strip()))
            for name, limit in limits.items())):
        raise ValueError('调用限额须为工具到非负整数或配置项名称的映射')
    if value.get('tool_registration') == 'managed' and value.get('execution') not in ('files', 'container'):
        raise ValueError('不支持的托管插件执行环境')
    return value
