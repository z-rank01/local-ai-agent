"""Offline unit tests for the SKILL.md (Agent Skills) package parser."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_DIR = ROOT / "skills" / "runner"
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

import skill_md  # noqa: E402


def write_package(root: Path, dirname: str, skill_md_text: str, files: dict[str, str] | None = None) -> Path:
    package_dir = root / dirname
    package_dir.mkdir(parents=True)
    (package_dir / "SKILL.md").write_text(skill_md_text, encoding="utf-8")
    for rel, content in (files or {}).items():
        target = package_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return package_dir


VALID = """---
name: demo-skill
description: >-
  演示技能包：用于验证 frontmatter 折叠语法
  跨多行的描述。
version: 1.2.3
---

# 演示

正文内容，不属于元数据。
"""


class SplitFrontmatterTests(unittest.TestCase):
    def test_valid_block(self):
        split = skill_md.split_frontmatter("---\nname: x\ndescription: y\n---\n\nbody\n")
        self.assertEqual(split, ("name: x\ndescription: y", "\nbody\n"))

    def test_crlf_tolerated(self):
        split = skill_md.split_frontmatter("---\r\nname: x\r\n---\r\n\r\nbody")
        self.assertEqual(split[0], "name: x")
        self.assertEqual(split[1], "\nbody")

    def test_missing_fence_returns_none(self):
        self.assertIsNone(skill_md.split_frontmatter("# no frontmatter\n"))
        self.assertIsNone(skill_md.split_frontmatter("---\nunterminated"))


class ParsePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def parse(self, dirname, text, files=None):
        directory = write_package(self.root, dirname, text, files)
        return skill_md.parse_package(directory, self.root)

    def test_valid_package_full(self):
        package, error = self.parse("demo-skill", VALID, {
            "references/guide.md": "guide",
            "references/deep/nested.md": "nested",
            "scripts/run.py": "print(1)",
            "assets/chart.png": "fake",
        })
        self.assertIsNone(error)
        self.assertEqual(package.name, "demo-skill")
        self.assertIn("验证 frontmatter", package.description)
        self.assertEqual(package.version, "1.2.3")
        self.assertIn("演示", package.body)
        self.assertNotIn("name: demo-skill", package.body)
        self.assertEqual(package.references, ["references/deep/nested.md", "references/guide.md"])
        self.assertEqual(package.scripts, ["scripts/run.py"])
        self.assertEqual(package.assets, ["assets/chart.png"])

    def test_missing_frontmatter(self):
        _, error = self.parse("plain", "# just markdown\n")
        self.assertIn("frontmatter", error)

    def test_missing_name(self):
        _, error = self.parse("no-name", "---\ndescription: x\n---\nbody")
        self.assertIn("name", error)

    def test_missing_description(self):
        _, error = self.parse("no-desc", "---\nname: no-desc\n---\nbody")
        self.assertIn("description", error)

    def test_name_directory_mismatch(self):
        _, error = self.parse("dir-name", "---\nname: other-name\ndescription: x\n---\nbody")
        self.assertIn("不一致", error)

    def test_invalid_yaml(self):
        _, error = self.parse("bad-yaml", "---\nname: [unclosed\ndescription: x\n---\nbody")
        self.assertIn("YAML", error)

    def test_path_escape_rejected(self):
        text = "---\nname: escape\ndescription: x\n---\nbody"
        package_dir, error = self.parse("escape", text)
        self.assertIsNone(error)
        outside = self.root.parent / package_dir.name
        _, error = skill_md.parse_package(outside, self.root)
        self.assertIn("根目录", error)

    def test_hidden_directories_skipped_by_discovery(self):
        write_package(self.root, "_draft", VALID)
        write_package(self.root, ".hidden", VALID)
        write_package(self.root, "good", "---\nname: good\ndescription: ok\n---\nbody")
        valid, invalid = skill_md.discover_packages(self.root)
        self.assertEqual([p.name for p in valid], ["good"])
        self.assertEqual(invalid, [])

    def test_invalid_packages_reported_not_dropped(self):
        write_package(self.root, "broken", "---\ndescription: no name\n---\nbody")
        valid, invalid = skill_md.discover_packages(self.root)
        self.assertEqual(valid, [])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["name"], "broken")
        self.assertFalse(invalid[0]["valid"])
        self.assertIn("name", invalid[0]["error"])

    def test_listing_entry_is_compact(self):
        package, _ = self.parse("demo-skill", VALID, {"references/a.md": "a", "scripts/s.py": "s"})
        entry = package.listing_entry()
        self.assertEqual(entry["name"], "demo-skill")
        self.assertEqual(entry["kind"], "skill_md")
        self.assertEqual(entry["version"], "1.2.3")
        self.assertEqual(entry["references_count"], 1)
        self.assertEqual(entry["scripts_count"], 1)
        self.assertNotIn("body", entry)
        self.assertNotIn("references", entry)


if __name__ == "__main__":
    unittest.main()
