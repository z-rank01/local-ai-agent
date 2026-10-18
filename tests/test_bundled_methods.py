import sys
import tempfile
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / 'skills' / 'runner'
sys.path.insert(0, str(RUNNER))
from bundled_methods import install_methods
from skill_md import parse_package


class BundledMethodsTests(unittest.TestCase):
    def test_installs_in_each_workspace_without_overwriting_user_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('default', 'named'):
                workspace = Path(directory) / name
                self.assertEqual(install_methods(workspace), ['harness-stock-research-v1'])
                target = workspace / 'skills/harness-stock-research-v1'
                package, error = parse_package(target, target.parent)
                self.assertIsNone(error)
                self.assertEqual(len(package.references), 7)
                edited = target / 'references/company.md'
                edited.write_text('User edit', encoding='utf-8')
                self.assertEqual(install_methods(workspace), [])
                self.assertEqual(edited.read_text(encoding='utf-8'), 'User edit')
                self.assertEqual(list(target.parent.glob('.install-*')), [])

    def test_original_finance_package_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            original = workspace / 'skills/wb-finance-skill/SKILL.md'
            original.parent.mkdir(parents=True)
            original.write_text('original', encoding='utf-8')
            install_methods(workspace)
            self.assertEqual(original.read_text(encoding='utf-8'), 'original')
