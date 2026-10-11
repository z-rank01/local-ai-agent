"""Fault replay of the candidate driver's actual class, without startup/network.

Set QUALITY_REPLAY_DRIVER and STOCK_REPLAY_TEST_ROOT for this integration gate.
Only the class AST is loaded: module-level credentials and preflight never run.
"""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

STOCK_ROOT = os.environ.get('STOCK_REPLAY_TEST_ROOT')
DRIVER = os.environ.get('QUALITY_REPLAY_DRIVER')


@unittest.skipUnless(STOCK_ROOT and DRIVER, 'requires stock checkout and candidate driver')
class ReplayDriverRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, STOCK_ROOT)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(STOCK_ROOT)

    def setUp(self):
        from scripts import quality_replay_policy
        from services.paper.common import digest
        from services.paper.data import atomic_json
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Mock()
        self.invoke = Mock(side_effect=AssertionError('existing reservation was reissued'))
        self.namespace = {'root': Path(self.directory.name), 'digest': digest,
            'json': json, 'deepcopy': deepcopy, 'SimpleNamespace': SimpleNamespace,
            'atomic_json': atomic_json, 'policy': quality_replay_policy,
            'store': self.store, 'invoke': self.invoke, 'preflight': Mock(),
            'snapshot': {'bundle_hash': 'frozen'}, 'manifest': {'batch': 'test'},
            'registry': SimpleNamespace(manifest=lambda: {'bundle_hash': 'frozen'})}
        tree = ast.parse(Path(DRIVER).read_text(encoding='utf-8-sig'))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ReplayRunner']
        self.assertEqual(len(classes), 1)
        exec(compile(ast.Module(body=classes, type_ignores=[]), DRIVER, 'exec'), self.namespace)
        self.runner = self.namespace['ReplayRunner']({'arm': 'quality'}, {})

    def recover(self, status, body, duty='C01'):
        self.store.one.return_value = {'status': status, 'body': json.dumps(body)}
        result = self.runner.run(duty, {}, request_id='existing-node', evidence_ids=[])
        self.invoke.assert_not_called()
        return result.output.model_dump()

    def test_crash_after_length_receipt_preserves_split_signal(self):
        result = self.recover('TRUNCATED', {'profile': 'standard',
            'reply': {'finish_reason': 'length'}})
        self.assertEqual(result['run_status'], 'FAILED')
        self.assertEqual(result['data'], {'truncated': True, 'finish_reason': 'length'})
        self.assertEqual(self.store.one.call_count, 1)

    def test_crash_after_known_rejection_remains_failed(self):
        result = self.recover('FAILED', {'reply': {'known_failure': True,
            'provider_error': 'PROVIDER_REJECTED'}, 'validation_error': 'unsupported parameter'})
        self.assertEqual(result['run_status'], 'FAILED')
        self.assertIn('unsupported parameter', result['missing'])
        self.assertEqual(self.store.one.call_count, 1)

    def test_running_and_unknown_reservations_never_reissue(self):
        for status in ('RUNNING', 'UNKNOWN'):
            with self.subTest(status=status):
                for cached in Path(self.directory.name).glob('node-*.json'):
                    cached.unlink()
                self.store.reset_mock()
                result = self.recover(status, {'reply': {'unfinished': True}})
                self.assertEqual(result['run_status'], 'UNKNOWN')
                self.assertEqual(self.store.one.call_count, 1)

    def test_expanded_c03_truncation_is_not_unknown(self):
        result = self.recover('TRUNCATED', {'profile': 'expanded',
            'reply': {'finish_reason': 'length'}}, duty='C03')
        self.assertEqual(result['run_status'], 'FAILED')
        self.assertTrue(result['data']['truncated'])

    def test_exhausted_format_repairs_restore_failure_without_fourth_call(self):
        result = self.recover('FAILED', {'validation_error': 'bad citation',
            'reply': {'finish_reason': 'stop', 'known_failure': False}})
        self.assertEqual(self.store.one.call_count, 3)
        self.assertEqual(result['run_status'], 'FAILED')

    def test_success_restores_saved_output(self):
        output = {'run_status': 'COMPLETED', 'data_quality': 'PARTIAL', 'data': {}}
        self.assertEqual(self.recover('SUCCEEDED', {'validated_output': output}), output)

    def test_standard_c03_length_restores_expansion_but_not_unknown_successor(self):
        self.store.one.side_effect = [
            {'status': 'TRUNCATED', 'body': json.dumps({'profile': 'standard',
                'reply': {'finish_reason': 'length'}})},
            {'status': 'UNKNOWN', 'body': json.dumps({'reply': {'unfinished': True}})}]
        result = self.runner.run('C03', {}, request_id='existing-node', evidence_ids=[])
        self.assertEqual(result.output.model_dump()['run_status'], 'UNKNOWN')
        self.assertEqual(self.store.one.call_count, 2)
        self.invoke.assert_not_called()

    def test_transport_decode_error_is_unknown_not_format_retry(self):
        from services.paper.common import RuleError, dumps, stamp
        from services.agents.contracts import Request, Scope
        from services.agents.registry import Registry
        from services.agents.adapters.specialist import material_packet
        registry = Registry(root=Path(STOCK_ROOT))
        self.namespace.update(registry=registry, Request=Request, Scope=Scope,
            material_packet=material_packet, RuleError=RuleError, dumps=dumps,
            stamp=stamp, time=time, state=Path(self.directory.name))
        self.namespace['snapshot']['bundle_hash'] = registry.manifest()['bundle_hash']
        self.namespace['snapshot']['execution_hash'] = 'frozen'
        self.store.one.return_value = None
        self.store.account.return_value = {'version': '2'}
        from unittest.mock import MagicMock
        self.store.tx.return_value = MagicMock()
        self.invoke.side_effect = ValueError('response JSON could not be decoded')
        self.runner.case = {'as_of': '2026-10-12T00:00:00+08:00'}
        self.runner.sample = {'arm': 'quality', 'case_id': 'Q', 'repetition': 1}
        material = {'research_question': '公开问题', 'coverage': {},
                    'evidence': {'mode': 'THEME_RESEARCH', 'events': []}}
        result = self.runner.run('C01', material, request_id='existing-node', evidence_ids=[])
        self.assertEqual(result.output.model_dump()['run_status'], 'UNKNOWN')
        self.assertEqual(self.invoke.call_count, 1)
        self.runner.run('C01', material, request_id='existing-node', evidence_ids=[])
        self.assertEqual(self.invoke.call_count, 1)
