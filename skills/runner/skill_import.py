"""Copy an explicitly selected workspace package without executing its contents."""
from pathlib import Path
import tempfile
import yaml

from bundled_methods import _publish
from sandbox import validate_workspace_cwd
from skill_paths import validate_name, checked_child
from skill_md import split_frontmatter, parse_package
from skill_versions import _contents


def import_directory(path: str, destination=Path('/workspace/skills')):
    source = Path(validate_workspace_cwd(path))
    contents = _contents(source)
    markers = [name for name in contents if name.lower() == 'skill.md']
    if len(markers) != 1:
        raise ValueError('导入目录必须包含唯一的 SKILL.md 或 skill.md')
    text = contents.pop(markers[0]).decode('utf-8-sig')
    parts = split_frontmatter(text)
    if parts is None:
        raise ValueError('技能说明缺少 YAML frontmatter')
    try:
        metadata = yaml.safe_load(parts[0])
    except yaml.YAMLError as exc:
        raise ValueError('技能说明的 YAML 格式无效，请修正后重新导入') from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get('name'), str):
        raise ValueError('技能缺少名称')
    name = validate_name(metadata['name'].strip().lower())
    metadata['name'] = name
    contents['SKILL.md'] = ('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
                            + '---\n' + parts[1]).encode('utf-8')
    destination.mkdir(parents=True, exist_ok=True)
    target = checked_child(destination, name)
    if target.exists():
        raise ValueError('同名技能已存在；请修改现有目录后检查更新，不覆盖用户文件')
    with tempfile.TemporaryDirectory(prefix='.import-', dir=destination) as staging:
        copied = Path(staging) / name
        copied.mkdir()
        for relative, data in contents.items():
            output = copied / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        package, error = parse_package(copied, Path(staging))
        if error:
            raise ValueError(error)
        _publish(copied, target)
    return {'name': name, 'path': str(target), 'imported': True}
