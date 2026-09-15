"""Agent Skills (SKILL.md) package support: discovery, frontmatter parsing, validation.

A standard skill package is a directory under /workspace/skills/ containing a
SKILL.md with YAML frontmatter (``name`` and ``description`` required) plus
optional ``references/``, ``scripts/`` and ``assets/`` subdirectories. This
module only reads and validates package structure — it never executes package
content. It is intentionally free of sandbox imports so it can be unit-tested
offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKAGE_MARK = "SKILL.md"
RESOURCE_DIRS = ("references", "scripts", "assets")
_LIST_DESCRIPTION_LIMIT = 2000
_RESOURCE_LISTING_LIMIT = 200


@dataclass(slots=True)
class SkillPackage:
    """Parsed, validated SKILL.md package metadata (progressive level 1 + body)."""

    name: str  # directory name — the unique key used by tools
    description: str
    body: str  # SKILL.md content without the frontmatter block
    references: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    version: str = ""
    path: str = ""

    def listing_entry(self) -> dict:
        """Compact metadata for skill_list (progressive disclosure level 1)."""
        entry = {
            "name": self.name,
            "description": self.description[:_LIST_DESCRIPTION_LIMIT],
            "kind": "skill_md",
        }
        if self.version:
            entry["version"] = self.version
        if self.references:
            entry["references_count"] = len(self.references)
        if self.scripts:
            entry["scripts_count"] = len(self.scripts)
        return entry


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Split SKILL.md text into (frontmatter_yaml, body).

    Returns None when the file does not start with a ``---`` frontmatter
    fence. Tolerates CRLF line endings.
    """
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n") and normalized.strip() != "---":
        return None
    rest = normalized[4:] if normalized.startswith("---\n") else ""
    if not rest:
        return "", ""
    end = rest.find("\n---")
    if end < 0:
        return None
    closing = rest.find("\n", end + 1)
    body = rest[closing + 1:] if closing >= 0 else ""
    return rest[:end], body


def parse_package(skill_dir: Path, skills_root: Path | None = None) -> tuple[SkillPackage | None, str | None]:
    """Parse and validate one skill package directory.

    Returns (package, None) on success, (None, error_message) on any
    validation failure. Errors are explicit by design: an invalid package is
    reported, never silently dropped.
    """
    try:
        skill_dir = skill_dir.resolve()
    except OSError as exc:
        return None, f"技能目录不可读: {exc}"
    if skills_root is not None:
        root = Path(skills_root).resolve()
        if skill_dir.parent != root:
            return None, "技能目录必须直接位于 skills 根目录下"

    name = skill_dir.name
    if not name or name.startswith("_") or name.startswith("."):
        return None, f"非法技能目录名: {name!r}"
    mark = skill_dir / PACKAGE_MARK
    if not mark.is_file():
        return None, f"缺少 {PACKAGE_MARK}"

    try:
        text = mark.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{PACKAGE_MARK} 读取失败: {exc}"

    split = split_frontmatter(text)
    if split is None:
        return None, f"{PACKAGE_MARK} 缺少 YAML frontmatter（--- 包围的元数据块）"
    front_text, body = split
    try:
        data = yaml.safe_load(front_text) or {}
    except yaml.YAMLError as exc:
        return None, f"frontmatter YAML 解析失败: {exc}"
    if not isinstance(data, dict):
        return None, "frontmatter 必须是 YAML 映射"

    fm_name = data.get("name")
    if not isinstance(fm_name, str) or not fm_name.strip():
        return None, "frontmatter 缺少必填字段 name"
    if fm_name.strip() != name:
        return None, f"frontmatter name({fm_name!r})与目录名({name!r})不一致"
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return None, "frontmatter 缺少必填字段 description"
    if "/" in fm_name or "\\" in fm_name or ".." in fm_name:
        return None, "name 字段不允许包含路径分隔符"

    package = SkillPackage(
        name=name,
        description=description.strip(),
        body=body.strip(),
        version=str(data.get("version", "") or ""),
        path=str(skill_dir),
    )
    for resource_dir in RESOURCE_DIRS:
        directory = skill_dir / resource_dir
        listing: list[str] = []
        if directory.is_dir():
            for child in sorted(directory.rglob("*")):
                if child.is_file():
                    listing.append(f"{resource_dir}/{child.relative_to(directory).as_posix()}")
                    if len(listing) >= _RESOURCE_LISTING_LIMIT:
                        break
        setattr(package, resource_dir, listing)
    return package, None


def discover_packages(skills_root: Path) -> tuple[list[SkillPackage], list[dict]]:
    """Scan a skills root for package directories.

    Returns (valid_packages, invalid_entries) where invalid_entries carry an
    explicit error so non-compliant packages are visible instead of ignored.
    """
    root = Path(skills_root)
    valid: list[SkillPackage] = []
    invalid: list[dict] = []
    if not root.is_dir():
        return valid, invalid
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if not (child / PACKAGE_MARK).is_file():
            continue
        package, error = parse_package(child, skills_root)
        if package is not None:
            valid.append(package)
        else:
            invalid.append({"name": child.name, "kind": "skill_md", "valid": False, "error": error})
    return valid, invalid
