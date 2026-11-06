import json
import sqlite3
import unittest
from unittest.mock import AsyncMock

from tests.finance_recipe_replay import inspect_role
from tests.subagent_fixtures import role
from tests.finance_recipe_probe import probe
from tests.finance_recipe_stages import review_rows
from harness.runtime.subagents.spec import load_role_specs
from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.results import structured_result
from pathlib import Path


class FinanceReplayTests(unittest.TestCase):
    def test_archived_failure_shapes_against_registered_contracts(self):
        plugins = Path(__file__).resolve().parents[1] / 'plugins'
        names = {name for p in plugins.glob('*/plugin.json')
                 for r in json.loads(p.read_text('utf8')).get('agent_roles', []) for name in r.get('tools', [])}
        roles = load_role_specs(plugins, names)
        calculation = {'id':'c1', 'operation':'sum', 'unit':'元', 'evidence_refs':[],
                       'inputs':[{'source':'S1','value':'5','unit':'元'}]}
        spec = roles['finance_calculation']
        raw = json.dumps({'artifacts':[{'kind':spec.output_artifact_kind,
                          'content':{'calculations':[calculation], 'figures':[]}}]})
        for name, text, message in [('finance_evidence','{"artifacts":[] "issues":[]}', 'delimiter'),
                                    ('finance_calculation',raw, 'period'),
                                    ('finance_calculation','{"artifacts":[]}]','Extra data'),
                                    ('finance_analysis','{"claims":[]}', 'artifacts')]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                structured_result(RoleTask(roles[name], '回放'), text, status='COMPLETE')

    def test_review_reference_requires_matching_candidate_and_evidence(self):
        sample = {'reviews': {'a':{'status':'COMPLETE','review_ref':'R','candidate_sha256':'C',
                                    'evidence_sha256':'E'}},
                  'publication_decisions':[{'review_ref':'R','candidate_sha256':'C','evidence_sha256':'old',
                                            'review_response':'采用'}]}
        self.assertFalse(review_rows(sample)[0]['matching_author_decision'])
    def inspect(self, status='FAILED', raw='bad', workspace='w', batch='batch'):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('create table task_evidence(id, conversation_id, scope, content)')
        result = {'role_id': 'tester', 'status': status, 'artifacts': [], 'raw_text': raw}
        db.execute('insert into task_evidence values(?,?,?,?)', ('ref', 'chat', 'cloud:w',
                   json.dumps({'batch_id': 'batch', 'result': result})))
        return inspect_role(db, {'conversation_id': 'chat', 'artifacts': {}}, workspace, batch,
                            role('tester'), {'result_ref': 'ref', 'status': status, 'artifact_refs': []}, False)

    def test_replay_separates_observed_failure_from_unknown(self):
        self.assertEqual(self.inspect()['replay'], 'CONTRACT_ERROR')
        self.assertEqual(self.inspect('UNKNOWN')['replay'], 'NO_CONFIRMED_OUTPUT')
        parsed = self.inspect(raw='{"artifacts": []}')
        self.assertEqual((parsed['recorded'], parsed['replay']), ('FAILED', 'PARSED'))

    def test_unrelated_workspace_or_batch_is_not_read_as_role_result(self):
        self.assertEqual(self.inspect(workspace='other')['replay'], 'ARCHIVE_UNAVAILABLE')
        self.assertEqual(self.inspect(batch='other')['replay'], 'IDENTITY_MISMATCH')


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_even_when_model_requests_another_action(self):
        call = AsyncMock(return_value={'status':'COMPLETE', 'text':'{"tool_calls":[]}'})
        result = await probe(None, {}, [{'role':'system','content':'s'}, {'role':'user','content':'u'}],
                             'one change', call=call)
        call.assert_awaited_once()
        self.assertIn('contract_error', result)

    async def test_unsupported_message_protocol_stops_before_dispatch(self):
        call = AsyncMock()
        with self.assertRaises(ValueError):
            await probe(None, {}, [{'role':'tool','content':'archive'}], 'one change', call=call)
        call.assert_not_awaited()
