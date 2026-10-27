"""Manifest-owned commands; no request-provided tools or executable payloads."""
import re

from harness.contracts.commands import Command
from harness.contracts.plugin_manifest import read_manifest


class CommandRegistry:
    def __init__(self):
        self.commands, self.errors = {}, {}

    @classmethod
    def bundled(cls, packages):
        registry = cls()
        for manifest in sorted(packages.glob('*/plugin.json')):
            identity = manifest.parent.name
            try:
                metadata = read_manifest(manifest, packages)
                declarations = metadata.get('commands', {})
                if not isinstance(declarations, dict):
                    raise ValueError('命令声明须为映射')
                owners = tuple(metadata.get('owners', metadata['backends']))
                for name, value in declarations.items():
                    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', name) or not isinstance(value, dict):
                        raise ValueError('命令标识或声明无效')
                    if (not isinstance(value.get('title'), str) or not value['title'].strip()
                            or not isinstance(value.get('tool'), str) or not value['tool']
                            or not isinstance(value.get('parameters', {}), dict)):
                        raise ValueError('命令缺少标题、工具或参数 schema')
                    command = Command(identity, name, value['title'], value['tool'],
                                      value.get('parameters', {}), owners)
                    registry.commands[(identity, name)] = command
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                registry.errors[identity] = str(exc)
        return registry

    def get(self, identity, name):
        if identity in self.errors:
            raise ValueError('能力命令声明不可用：' + self.errors[identity])
        try:
            return self.commands[(identity, name)]
        except KeyError:
            raise KeyError('未登记的插件命令') from None

    @staticmethod
    def validate(command, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('命令参数须为对象')
        schema = command.parameters
        properties = schema.get('properties', {})
        if not isinstance(properties, dict):
            raise ValueError('命令参数 schema 无效')
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise ValueError('命令包含未声明参数：' + ', '.join(unknown))
        values = {name: value['default'] for name, value in properties.items()
                  if isinstance(value, dict) and 'default' in value}
        values.update(arguments)
        required = schema.get('required', [])
        if not isinstance(required, list) or any(name not in values for name in required):
            raise ValueError('命令缺少必填参数')
        expected = {'string': str, 'boolean': bool, 'integer': int, 'number': (int, float),
                    'array': list, 'object': dict}
        for name, value in values.items():
            definition = properties[name]
            kind = definition.get('type', 'string')
            if kind not in expected:
                raise ValueError('命令参数类型不支持：' + kind)
            if (kind in ('integer', 'number') and isinstance(value, bool)) or not isinstance(value, expected[kind]):
                raise ValueError('命令参数类型错误：' + name)
            if 'enum' in definition and value not in definition['enum']:
                raise ValueError('命令参数取值无效：' + name)
        return values
