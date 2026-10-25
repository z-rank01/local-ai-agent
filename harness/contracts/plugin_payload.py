"""Validate imported resource references without loading executable modules."""
import re
import yaml


def validate_payload(root, metadata):
    try:
        _validate_payload(root, metadata)
    except (TypeError, AttributeError, KeyError, yaml.YAMLError) as exc:
        raise ValueError('插件贡献声明格式无效：' + str(exc)) from exc


def _validate_payload(root, metadata):
    identity = metadata['id']

    def resource(relative):
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('插件资源缺失或越界：' + str(relative))
        return path

    def provider(reference):
        module, separator, name = reference.partition(':')
        prefix = 'plugins.' + identity + '.'
        if not separator or not module.startswith(prefix) or not name.isidentifier():
            raise ValueError('提供者入口必须属于当前插件')
        relative = module[len(prefix):].replace('.', '/')
        resource(relative + '.py')

    if metadata.get('tool_directory', 'tools') != 'tools':
        raise ValueError('首版托管插件工具须放在 tools 目录')
    definitions = list((root / 'tools').glob('*.yaml'))
    if not definitions:
        raise ValueError('插件缺少工具声明')
    names = set()
    backend = 'skill-files' if metadata['execution'] == 'files' else identity
    for file in definitions:
        tool = yaml.safe_load(file.read_text(encoding='utf-8'))
        name = tool.get('name') if isinstance(tool, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name) or name in names:
            raise ValueError('插件工具名称无效或重复')
        names.add(name)
        if tool.get('backend') != backend or not isinstance(tool.get('parameters'), dict):
            raise ValueError('插件工具后端或输入 schema 无效')
    if metadata['execution'] == 'files':
        resource('runtime/provider.py')
    else:
        provider(metadata.get('transport', ''))
        services = metadata.get('services', {})
        service = services.get(metadata.get('service')) if isinstance(services, dict) else None
        if not isinstance(service, dict):
            raise ValueError('容器插件缺少所声明服务')
        resource(service.get('dockerfile', ''))
        if type(service.get('port')) is not int or not 1024 <= service['port'] <= 65535:
            raise ValueError('容器内部端口无效')
    if metadata.get('task_provider'):
        provider(metadata['task_provider'])
    if metadata.get('artifacts'):
        provider(metadata['artifacts']['provider'])
    for prompt in metadata.get('prompts', []):
        resource(prompt['path'])
    if metadata.get('ui', {}).get('settings'):
        entry = metadata['ui']['settings']
        prefix = 'plugins/' + identity + '/ui/'
        if not entry.startswith(prefix) or not entry.endswith('.tsx'):
            raise ValueError('导入插件界面须放在自身 ui 目录')
        resource(entry[len('plugins/' + identity + '/'):])
