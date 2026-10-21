"""Content-addressed Skill copies. No imports, execution or dependency installs."""
import hashlib
from pathlib import Path
import tempfile

import skill_md
from bundled_methods import _publish
from skill_paths import checked_child, validate_name

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000


def _contents(source: Path) -> dict[str, bytes]:
    contents, size = {}, 0
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('技能版本不能包含链接或越界路径')
        if path.is_file():
            if len(contents) >= MAX_FILES or size + path.stat().st_size > MAX_PACKAGE_BYTES:
                raise ValueError('技能包超过文件数或 32 MiB 大小限制')
            data = path.read_bytes()
            size += len(data)
            if size > MAX_PACKAGE_BYTES:
                raise ValueError('读取期间技能包超过大小限制')
            contents[relative.as_posix()] = data
    return contents


def _digest(contents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, data in sorted(contents.items()):
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big') + encoded)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


class SkillVersions:
    def __init__(self, source: Path, storage: Path):
        self.source, self.storage = source, storage

    def capture(self, name: str) -> dict:
        source = checked_child(self.source, validate_name(name))
        package, error = skill_md.parse_package(source, self.source)
        if error:
            raise ValueError(error)
        contents = _contents(source)
        revision = _digest(contents)
        self.storage.mkdir(parents=True, exist_ok=True)
        target = self.storage / revision
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='.capture-', dir=self.storage) as staging:
                staged = Path(staging) / revision
                copied = staged / 'skills' / name
                copied.mkdir(parents=True)
                for relative, data in contents.items():
                    destination = copied / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                # Validate the captured bytes, not the still mutable source.
                captured, error = skill_md.parse_package(copied, copied.parent)
                if error:
                    raise ValueError(error)
                if _digest(_contents(source)) != revision:
                    raise ValueError('技能在读取期间发生变化，请重新检查')
                _publish(staged, target)
        captured = self.read(name, revision)
        return {**captured.listing_entry(), 'revision': revision, 'path': captured.path}

    def read(self, name: str, revision: str):
        import re
        validate_name(name)
        if not re.fullmatch('[0-9a-f]{64}', revision):
            raise ValueError('无效技能版本')
        version = checked_child(self.storage, revision)
        package_root = checked_child(version, 'skills')
        package_path = checked_child(package_root, name)
        if _digest(_contents(package_path)) != revision:
            raise ValueError('技能版本缺失或内容已变化，不能使用此版本')
        package, error = skill_md.parse_package(package_path, package_root)
        if error:
            raise ValueError(error)
        return package

    def freeze(self) -> dict:
        entries, invalid = skill_md.discover_packages(self.source)
        versions = {}
        for package in entries:
            try:
                versions[package.name] = self.capture(package.name)
            except (ValueError, OSError) as exc:
                invalid.append({'name': package.name, 'kind': 'skill_md', 'valid': False, 'error': str(exc)})
        return {'versions': versions, 'invalid': invalid}
