"""Run with STOCK_REPLAY_TEST_ROOT set to the connected stock checkout."""
import copy
import os
from pathlib import Path
import sys
import unittest

STOCK_ROOT = os.environ.get('STOCK_REPLAY_TEST_ROOT')


@unittest.skipUnless(STOCK_ROOT and Path(STOCK_ROOT).is_dir(), 'requires connected stock checkout')
class ReplayPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, STOCK_ROOT)

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
