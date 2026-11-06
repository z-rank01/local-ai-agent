import json
import sqlite3
import unittest

from tests.finance_recipe_replay import inspect_role
from tests.subagent_fixtures import role


class FinanceReplayTests(unittest.TestCase):
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
