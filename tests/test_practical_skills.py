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

    def test_capability_dependencies_and_fixed_script_entrypoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_methods(root)
            compare, error = parse_package(root / 'skills/document-compare', root / 'skills')
            self.assertIsNone(error)
            self.assertEqual(compare.required, [])
            self.assertEqual(compare.optional, ['document-index', 'websearch'])
            table, error = parse_package(root / 'skills/table-reconcile', root / 'skills')
            self.assertIsNone(error)
            entry = table.listing_entry()
            self.assertEqual(len(entry['entrypoints']), 1)
            self.assertEqual(Path(entry['entrypoints'][0]['path']), Path(table.path) / 'scripts/reconcile.py')

    def test_holdout_verifies_shell_read_and_saved_artifact(self):
        from hashlib import sha256
        from scripts.harness_holdout import structure
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'cases/meeting-notes/m1-v1/record.md'
            source.parent.mkdir(parents=True)
            source.write_text('这是一次真实的会议记录，包含足够长的原文用于核对，不能只凭工具名称宣称已经读取。', encoding='utf-8')
            output = source.with_name('minutes.md')
            output.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
            task = {'capability': 'meeting-notes', 'task': 'm1'}
            inputs = [{'path': '/workspace/cases/meeting-notes/m1-v1/record.md', 'sha256': sha256(source.read_bytes()).hexdigest()}]
            rows = [{'role': 'tool', 'tool_name': 'skill_info', 'tool_result': {'body': '方法'}},
                    {'role': 'tool', 'tool_name': 'shell_exec',
                     'params': {'command': 'cat /workspace/cases/meeting-notes/m1-v1/record.md && cat /workspace/cases/meeting-notes/m1-v1/minutes.md'},
                     'tool_result': {'stdout': source.read_text(encoding='utf-8'), 'exit_code': 0}}]
            checks = structure(task, rows, inputs, root)
            self.assertTrue(checks['required_tools_present'])
            self.assertTrue(checks['source_reread'])
            self.assertTrue(checks['written_artifact_reread'])

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


class HoldoutEvidenceTests(unittest.TestCase):
    def test_index_receipt_requires_matching_source_hash_and_actual_text(self):
        from hashlib import sha256
        from scripts.harness_holdout import structure
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'cases/document-index/i1-v1/example.txt'
            source.parent.mkdir(parents=True)
            source.write_text('实际原文窗口', encoding='utf-8')
            fingerprint = sha256(source.read_bytes()).hexdigest()
            path = '/workspace/cases/document-index/i1-v1/example.txt'
            task = {'capability': 'document-index', 'task': 'i1'}
            inputs = [{'path': path, 'sha256': fingerprint}]
            rows = [{'role': 'tool', 'tool_name': 'documents_index', 'tool_result': {'indexed': 1}},
                    {'role': 'tool', 'tool_name': 'documents_find', 'tool_result': {
                        'matches': [{'path': path, 'source_version': fingerprint, 'text': '实际原文窗口'}]}}]
            self.assertTrue(structure(task, rows, inputs, root)['source_reread'])
            rows[1]['tool_result']['matches'][0]['source_version'] = '0' * 64
            self.assertFalse(structure(task, rows, inputs, root)['source_reread'])

    def test_summary_preserves_all_attempts_unknowns_and_sample_kinds(self):
        from scripts.harness_holdout import summarize
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'data/private/harness-holdout/local'
            def row(variant, status, stream='STREAM_COMPLETE'):
                return {'variant': variant, 'key': 'meeting-notes/m1', 'capability': 'meeting-notes',
                        'task': 'm1', 'model': 'qwen', 'workspace_id': 'test', 'status': stream,
                        'run': {'status': status}, 'checks': {'originals_unchanged': True,
                            'required_tools_present': True, 'written_artifact_reread': True, 'source_reread': True}}
            for variant, rows, kind in [('v1', [row('v1', 'SUCCEEDED'), row('v1', 'FAILED', 'UNKNOWN')], 'holdout'),
                                        ('v2', [row('v2', 'SUCCEEDED')], None)]:
                directory = base / variant
                directory.mkdir(parents=True)
                (directory / 'runs.json').write_text(json.dumps(rows), encoding='utf-8')
                if kind:
                    (directory / 'case.json').write_text(json.dumps({'sample_kind': kind}), encoding='utf-8')
            result = summarize('local', root=root)
            self.assertEqual(result['summary']['attempts'], 3)
            self.assertEqual(result['summary']['by_model']['qwen']['run_succeeded'], 2)
            self.assertEqual(result['summary']['by_model']['qwen']['unknown'], 1)
            self.assertEqual(result['summary']['by_model']['qwen']['engineering_pass'], 2)
            self.assertEqual(result['summary']['by_model']['qwen']['content_review_pending'], 3)
            self.assertEqual(result['holdout_summary']['attempts'], 2)
            self.assertEqual(result['summary']['sample_kinds'], {'development': 1, 'holdout': 2})
            self.assertEqual(summarize('local', variants={'v1'}, root=root)['summary']['attempts'], 2)
