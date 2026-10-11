"""Run with STOCK_REPLAY_TEST_ROOT set to the connected stock checkout."""
import copy
import os
from pathlib import Path
import sys
import unittest

STOCK_ROOT = os.environ.get('STOCK_REPLAY_TEST_ROOT')


@unittest.skipUnless(STOCK_ROOT and Path(STOCK_ROOT).is_dir(), 'requires connected stock checkout')
class ReplayPolicyTests(unittest.TestCase):
    def test_blind_review_hash_matches_stock_frozen_material(self):
        from scripts.quality_review import digest as review_digest
        from services.paper.common import digest as stock_digest
        material = {'research_question': '核对反证', 'evidence': {'values': [1, 2.5, None]},
                    'method': {'version': 'v1', 'body': '来源与日期'}}
        self.assertEqual(review_digest(material), stock_digest(material))

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, STOCK_ROOT)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(STOCK_ROOT)

    def setUp(self):
        from datetime import datetime, timezone
        from services.agents.contracts import Request, Scope
        from services.agents.registry import Registry
        self.registry = Registry(root=Path(STOCK_ROOT))
        manifest = self.registry.manifest()
        self.request = Request(request_id='replay-test', duty_id='C01', duty_version='1',
            bundle_hash=manifest['bundle_hash'], model_call_budget=3,
            as_of=datetime.now(timezone.utc), evidence_ids=['event'],
            scope=Scope(account_id='paper', mode='paper', strategy_version='2',
                policy_hash=manifest['policy_hash'], research_id='test', subject_id='theme'),
            material={'research_question': '公开问题', 'coverage': {},
                'evidence': {'mode': 'THEME_RESEARCH', 'events': [{'event_id': 'event',
                    'source': 'industry', 'facts': [{'quote': '公开原文'}]}]}})
        self.good = {'name': '主题', 'thesis': '待核对', 'counter': '需求下降',
                     'watch': '核对订单', 'evidence_ids': ['event']}
        self.output = {'run_status': 'COMPLETED', 'data_quality': 'COMPLETE',
            'verdict': None, 'report': '原叙述', 'evidence_refs': ['event'], 'missing': [],
            'data': {'trends': [self.good, {**self.good, 'name': '错误', 'evidence_ids': ['invented']}]}}

    def test_real_contract_exposes_treatment_difference_without_changing_input(self):
        from scripts.quality_replay_policy import validate_result
        from services.paper.common import RuleError
        before = copy.deepcopy(self.output)
        with self.assertRaises(RuleError):
            validate_result(self.registry, self.request, self.output, arm='original', final_repair=True)
        result = validate_result(self.registry, self.request, self.output, arm='quality', final_repair=True)
        self.assertEqual(result['data']['trends'], [self.good])
        self.assertEqual(result['data_quality'], 'PARTIAL')
        self.assertEqual(self.output, before)

    def test_repair_limit_and_unknown_are_not_bypassed(self):
        from scripts.quality_replay_policy import validate_result
        from services.paper.common import RuleError
        with self.assertRaises(RuleError):
            validate_result(self.registry, self.request, self.output, arm='quality', final_repair=False)
        unknown = {**self.output, 'run_status': 'UNKNOWN', 'data_quality': 'MISSING',
                   'missing': ['原调用结果未知'], 'data': {}}
        result = validate_result(self.registry, self.request, unknown, arm='quality', final_repair=True)
        self.assertEqual(result['run_status'], 'UNKNOWN')

    def test_valid_result_is_identical_across_arms(self):
        from scripts.quality_replay_policy import validate_result
        self.output['data']['trends'] = [self.good]
        results = [validate_result(self.registry, self.request, self.output, arm=arm)
                   for arm in ('original', 'quality')]
        self.assertEqual(results[0], results[1])

    def test_execution_uses_real_hierarchy_and_keeps_unknown_on_resume(self):
        import tempfile
        from types import SimpleNamespace
        from scripts.quality_replay_policy import execute_fixed_case
        from services.agents.contracts import Output
        from services.paper.common import digest
        material = copy.deepcopy(self.request.material)
        material['evidence']['events'][0]['facts'][0]['quote'] = '公开正文。' * 5000
        case = {'role': 'C01', 'material': material, 'input_hash': digest(material),
                'evidence_ids': ['event']}
        execution = {'input_chars': 6000, 'execution_hash': 'frozen-test'}
        for arm in ('original', 'quality'):
            calls = []
            class Runner:
                def check_cancelled(self, job_id):
                    pass

                def run(self, duty, node, **kwargs):
                    calls.append(kwargs['request_id'])
                    status = 'UNKNOWN' if len(calls) == 1 else 'COMPLETED'
                    return SimpleNamespace(output=Output(run_status=status,
                        data_quality='PARTIAL', report='offline transport fixture',
                        evidence_refs=[], missing=['待核验'], data={}))
            with tempfile.TemporaryDirectory() as directory:
                sample = {'arm': arm, 'blind_id': 'frozen-test'}
                runner = Runner()
                result = execute_fixed_case(runner, case, sample, execution, directory)
                self.assertEqual(result['run_status'], 'UNKNOWN')
                if arm == 'original':
                    self.assertEqual(len(calls), 1)
                else:
                    self.assertGreater(len(calls), 1)
                    count = len(calls)
                    again = execute_fixed_case(runner, case, sample, execution, directory)
                    self.assertEqual(again['run_status'], 'UNKNOWN')
                    self.assertEqual(len(calls), count)
                    self.assertEqual(len(calls), len(set(calls)))

    def test_execution_rejects_changed_evidence_before_transport(self):
        import tempfile
        from unittest.mock import Mock
        from scripts.quality_replay_policy import execute_fixed_case
        from services.paper.common import RuleError, digest
        material = copy.deepcopy(self.request.material)
        case = {'role': 'C01', 'material': material, 'input_hash': digest(material),
                'evidence_ids': ['event']}
        material['research_question'] = 'different question'
        runner = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuleError, 'evidence changed'):
                execute_fixed_case(runner, case, {'arm': 'quality', 'blind_id': 'x'},
                                   {'input_chars': 6000, 'execution_hash': 'frozen'}, directory)
        runner.run.assert_not_called()

    def test_company_execution_reuses_receipt_and_rejects_changed_treatment(self):
        import tempfile
        from types import SimpleNamespace
        from scripts.quality_replay_policy import execute_fixed_case
        from services.agents.contracts import Output
        from services.paper.common import RuleError, digest
        # Small production C03 material: no invented valuation or financial facts.
        material = {'instrument': {'symbol': '600000'}, 'evidence': {
            'positions': [], 'financial': {'facts': [], 'periods': [], 'raw_statements': {}}}}
        case = {'role': 'C03', 'material': material, 'input_hash': digest(material),
                'evidence_ids': []}
        calls = []
        class Runner:
            def check_cancelled(self, job_id):
                pass

            def run(self, duty, node, **kwargs):
                calls.append(kwargs['request_id'])
                return SimpleNamespace(request=SimpleNamespace(request_id=kwargs['request_id']),
                    output=Output(run_status='COMPLETED', data_quality='PARTIAL',
                                  report='offline receipt', evidence_refs=[], missing=[], data={}))
        with tempfile.TemporaryDirectory() as directory:
            sample = {'arm': 'quality', 'blind_id': 'company'}
            execution = {'input_chars': 6000, 'execution_hash': 'frozen'}
            runner = Runner()
            execute_fixed_case(runner, case, sample, execution, directory)
            execute_fixed_case(runner, case, sample, execution, directory)
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(RuleError, 'checkpoint'):
                execute_fixed_case(runner, case, {**sample, 'arm': 'original'}, execution, directory)
            self.assertEqual(len(calls), 1)
