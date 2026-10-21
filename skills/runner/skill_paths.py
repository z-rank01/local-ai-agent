"""Validate skill identities before any filesystem or execution side effect."""
import re
from pathlib import Path


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r'[\w-]{1,128}', name):
        raise ValueError('技能名称只能包含字母、数字、下划线和连字符；不能包含路径')
    return name


def checked_child(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('技能路径必须直接位于所属目录中，不能使用链接')
    return path
