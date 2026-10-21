"""Functional package import, read-only reconciliation and identity checks."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / 'skills/runner'
sys.path.insert(0, str(RUNNER))
from bundled_methods import install_methods
from skill_md import parse_package
import skill_registry
from skill_import import import_directory


class PracticalSkillsTests(unittest.TestCase):
    def test_import_normalizes_marker_without_executing_or_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'download'
            source.mkdir()
            (source / 'skill.md').write_text('---\nname: Meeting\ndescription: 整理\n---\n正文', encoding='utf-8')
            (source / 'scripts').mkdir()
            (source / 'scripts/run.py').write_text('raise RuntimeError("must not execute")', encoding='utf-8')
            with patch('sandbox._WORKSPACE_ROOT', str(root)):
                result = import_directory(str(source), root / 'skills')
                self.assertEqual(result['name'], 'meeting')
                self.assertTrue((root / 'skills/meeting/SKILL.md').is_file())
                with self.assertRaisesRegex(ValueError, '同名'):
                    import_directory(str(source), root / 'skills')
                with self.assertRaises(ValueError):
                    import_directory(str(root.parent), root / 'skills')
            self.assertTrue((source / 'skill.md').is_file())
            (source / 'skill.md').write_text('---\nname: [broken\n---\n正文', encoding='utf-8')
            with patch('sandbox._WORKSPACE_ROOT', str(root)):
                with self.assertRaisesRegex(ValueError, 'YAML'):
                    import_directory(str(source), root / 'skills')
            self.assertEqual([entry.name for entry in (root / 'skills').iterdir()], ['meeting'])

    def test_import_and_reconcile_without_altering_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_methods(root)
            for name in ('meeting-notes', 'table-reconcile', 'document-compare'):
                package, error = parse_package(root / 'skills' / name, root / 'skills')
                self.assertIsNone(error)
                self.assertTrue(package.body)
            left, right = root / 'left.csv', root / 'right.csv'
            left.write_text('单号,金额\na,0.10\nb,123456789012345678901234567890.01\nc,1\nc,2\nd,\ne,5\n', encoding='utf-8-sig')
            right.write_text('单号,金额\na,0.1\nb,123456789012345678901234567890.03\nc,3\nd,0\nf,6\n', encoding='utf-8')
            before = (left.read_bytes(), right.read_bytes())
            output = root / 'result.json'
            command = [sys.executable, str(root / 'skills/table-reconcile/scripts/reconcile.py'),
                       str(left), str(right), '--key', '单号', '--columns', '金额', '--output', str(output)]
            completed = subprocess.run(command, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual([r['reason'] for r in result['differences']],
                             ['value_difference', 'missing_right', 'missing_left'])
            self.assertEqual(result['differences'][0]['right_minus_left'], '0.02')
            self.assertEqual([r['reason'] for r in result['unresolved']], ['duplicate_key', 'invalid_number'])
            self.assertEqual((left.read_bytes(), right.read_bytes()), before)
            saved = output.read_bytes()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), saved)

    def test_invalid_identity_never_mutates_or_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / 'skills'
            skills.mkdir()
            original = skills / 'demo.py'
            original.write_text('untouched', encoding='utf-8')
            with patch.object(skill_registry, '_SKILLS_DIR', skills), patch.object(
                    skill_registry, '_REGISTRY_SKILLS_DIR', root / 'registry'), patch('subprocess.Popen') as execute:
                for name in ('../demo', '..\\demo', '/demo', 'a/b', 'C:demo', 'demo '):
                    for call in (lambda: skill_registry.skill_info(name),
                                 lambda: skill_registry.unregister_skill(name),
                                 lambda: skill_registry.update_skill(name, code='changed'),
                                 lambda: skill_registry.register_skill(name, code='changed'),
                                 lambda: skill_registry.run_skill(name, {})):
                        with self.subTest(name=name, call=call):
                            self.assertIn('error', call())
                execute.assert_not_called()
            self.assertEqual(original.read_text(encoding='utf-8'), 'untouched')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['skills'])
