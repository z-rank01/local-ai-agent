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
                self.assertEqual(install_methods(workspace), ['harness-public-sources-v1', 'harness-public-sources-v2', 'harness-stock-research-v1', 'harness-stock-research-v2', 'harness-stock-research-v3', 'harness-stock-research-v4'])
                target = workspace / 'skills/harness-stock-research-v1'
                package, error = parse_package(target, target.parent)
                self.assertIsNone(error)
                self.assertEqual(len(package.references), 7)
                edited = target / 'references/company.md'
                edited.write_text('User edit', encoding='utf-8')
                self.assertEqual(install_methods(workspace), [])
                self.assertEqual(edited.read_text(encoding='utf-8'), 'User edit')
                self.assertEqual(list(target.parent.glob('.install-*')), [])
                self.assertFalse(list(target.parent.rglob('*.pyc')))

    def test_new_package_installs_alongside_edited_previous_version(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            edited = workspace / 'skills/harness-stock-research-v2/references/finance.md'
            edited.parent.mkdir(parents=True)
            edited.write_text('User finance method', encoding='utf-8')
            self.assertEqual(install_methods(workspace), ['harness-public-sources-v1', 'harness-public-sources-v2', 'harness-stock-research-v1', 'harness-stock-research-v3', 'harness-stock-research-v4'])
            self.assertEqual(edited.read_text(encoding='utf-8'), 'User finance method')
            self.assertTrue((workspace/'skills/harness-stock-research-v3/scripts/extract.py').is_file())

    def test_original_finance_package_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            original = workspace / 'skills/wb-finance-skill/SKILL.md'
            original.parent.mkdir(parents=True)
            original.write_text('original', encoding='utf-8')
            install_methods(workspace)
            self.assertEqual(original.read_text(encoding='utf-8'), 'original')
