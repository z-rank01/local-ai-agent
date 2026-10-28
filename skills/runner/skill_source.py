"""Edit existing package text without executing it or touching frozen revisions."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading

from skill_paths import checked_child, validate_name
from skill_md import parse_package
from skill_versions import _contents

LIMIT = 256 * 1024
TEXT = {'.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.py', '.js', '.ts', '.sh', '.ps1', '.csv'}
_lock = threading.RLock()


class SourceConflict(ValueError):
    pass


def package_root(source, name):
    root = checked_child(source, validate_name(name))
    if not root.is_dir():
        raise ValueError('源目录缺失或为旧式 Python Skill；本次编辑器仅支持标准目录包')
    return root


def source_file(source, name, relative):
    root = package_root(source, name)
    parts = PurePosixPath(relative)
    if not relative or '\\' in relative or parts.is_absolute() or any(p in ('.', '..') for p in relative.split('/')):
        raise ValueError('文件必须是技能源目录内的相对路径')
    path = root
    for part in parts.parts:
        path = checked_child(path, part)
    if not path.is_file():
        raise ValueError('仅可编辑已存在的源文件，不创建或删除文件')
    return path


def read_file(source, name, relative):
    path = source_file(source, name, relative)
    size = path.stat().st_size
    reason = ''
    content = None
    if size > LIMIT:
        reason = '文件超过 256 KiB，请使用源文件编辑器'
    else:
        raw = path.read_bytes()
        try:
            content = raw.decode('utf-8')
            if '\x00' in content:
                content = None
                reason = '二进制资源只读'
        except UnicodeDecodeError:
            reason = '仅支持 UTF-8 文本'
    if not reason and path.suffix.lower() not in TEXT:
        reason = '此文件类型只读'
    return {'path': relative, 'content': content, 'size': size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if size <= LIMIT else None,
            'writable': not reason, 'reason': reason}


def list_files(source, name):
    root = package_root(source, name)
    contents = _contents(root)  # Existing package limits and link checks.
    rows = []
    for relative in sorted(contents, key=lambda name: (name != 'SKILL.md', name)):
        row = read_file(source, name, relative)
        rows.append({key: value for key, value in row.items() if key != 'content'})
    return {'files': rows}


def save_file(versions, name, relative, content, expected_sha256):
    raw = content.encode('utf-8')
    if len(raw) > LIMIT or '\x00' in content:
        raise ValueError('仅可保存不超过 256 KiB 的 UTF-8 文本')
    with _lock:
        before = read_file(versions.source, name, relative)
        if not before['writable']:
            raise ValueError(before['reason'])
        if before['sha256'] != expected_sha256:
            raise SourceConflict('源文件已被其他操作修改；草稿保留，请重新加载后比较')
        root = package_root(versions.source, name)
        contents = _contents(root)
        contents[relative] = raw
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / name
            for file, data in contents.items():
                target = candidate / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            _, error = parse_package(candidate)
            if error:
                raise ValueError('未保存：' + error)
        path = source_file(versions.source, name, relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise SourceConflict('校验期间源文件发生变化；未覆盖源文件')
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.source-', delete=False) as file:
                temporary_path = Path(file.name)
                file.write(raw)
            temporary_path.chmod(path.stat().st_mode & 0o777)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        revision = versions.capture(name)
        return {**read_file(versions.source, name, relative), 'revision': revision['revision']}
