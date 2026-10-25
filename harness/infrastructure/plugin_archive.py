"""Bounded, data-only package copies. Import never imports Python or runs a build."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024


def _name(value):
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or any(part in ('', '.', '..') or ':' in part
            or part.endswith((' ', '.')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', part, re.I)
            for part in value.split('/'))):
        raise ValueError('插件包含非法相对路径')
    return path.as_posix()


def read_package(source: Path):
    """Return portable names/bytes; reject links, collisions and oversized input."""
    files, names, spelling, total = {}, set(), {}, 0

    def add(name, data):
        nonlocal total
        name = _name(name)
        if name.casefold() in names:
            raise ValueError('插件文件存在大小写冲突或重复路径')
        parts = name.split('/')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            prior = spelling.setdefault(prefix.casefold(), prefix)
            if prior != prefix:
                raise ValueError('插件目录存在大小写冲突')
        total += len(data)
        if total > MAX_BYTES or len(files) >= MAX_FILES:
            raise ValueError('插件超过文件数量或总大小限制')
        names.add(name.casefold())
        files[name] = data

    if source.is_dir():
        if source.is_symlink() or getattr(source.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('插件来源不能是链接或重解析点')
        entries = 0
        for directory, directories, filenames in os.walk(source, followlinks=False):
            entries += len(directories) + len(filenames)
            if entries > MAX_FILES:
                raise ValueError('插件目录条目过多')
            for name in [*directories, *filenames]:
                entry = Path(directory) / name
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('插件目录不能包含链接或重解析点')
            for name in filenames:
                entry = Path(directory) / name
                if not entry.is_file() or entry.stat().st_size > MAX_BYTES - total:
                    raise ValueError('插件包含特殊文件或超过大小限制')
                with entry.open('rb') as handle:
                    add(entry.relative_to(source).as_posix(), handle.read(MAX_BYTES - total + 1))
    elif source.suffix.lower() == '.zip':
        with zipfile.ZipFile(source) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise ValueError('插件压缩包条目过多')
            for entry in archive.infolist():
                _name(entry.filename.rstrip('/'))
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('插件压缩包不能包含链接')
                if entry.is_dir():
                    continue
                if entry.file_size > MAX_BYTES - total:
                    raise ValueError('插件压缩包展开后超过大小限制')
                with archive.open(entry) as handle:
                    add(entry.filename, handle.read(MAX_BYTES - total + 1))
    else:
        raise ValueError('插件来源须为本地目录或 ZIP 包')
    if 'plugin.json' not in files:
        raise ValueError('包根目录缺少 plugin.json')
    return files


def content_revision(files):
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        raw = name.encode('utf-8')
        digest.update(len(raw).to_bytes(8, 'big') + raw + len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()
