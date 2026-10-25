import copy
import json
import unittest

from plugins.finance.receipts import paired_receipts


def rows():
    return [
        {'role': 'assistant', 'tool_calls': [{'id': 'read1', 'function': {
            'name': 'stock_public_evidence_read', 'arguments': json.dumps({'reference': 'a' * 64})}}]},
        {'role': 'tool', 'tool_name': 'stock_public_evidence_read', 'tool_call_id': 'read1',
         'content': json.dumps({'model_observation': {'kind': 'public_evidence_read', 'source_reference': 'a' * 64}})},
    ]


class ReceiptTests(unittest.TestCase):
    def test_exact_and_legacy_leave_input_unchanged(self):
        history = rows(); original = copy.deepcopy(history)
        self.assertEqual(len(list(paired_receipts(history))), 1)
        self.assertEqual(history, original)
        history[1]['content'] = json.dumps({'kind': 'public_evidence_read'})
        self.assertEqual(len(list(paired_receipts(history))), 1)
        self.assertEqual(list(paired_receipts(history, allow_legacy_reference=False)), [])

    def test_failed_inner_outer_wrong_kind_and_reference(self):
        for patch in ({'status': 'failed'}, {'error': 'x'}, {'source_reference': 'b' * 64},
                      {'kind': 'local_report_page'}):
            with self.subTest(patch=patch):
                history = rows()
                value = json.loads(history[1]['content'])
                value['model_observation'].update(patch)
                history[1]['content'] = json.dumps(value)
                self.assertEqual(list(paired_receipts(history)), [])
        history = rows(); value = json.loads(history[1]['content']); value['status'] = 'ERROR'
        history[1]['content'] = json.dumps(value)
        self.assertEqual(list(paired_receipts(history)), [])

    def test_pairing_and_conflicting_ids(self):
        for key, value in [('tool_call_id', ''), ('tool_name', 'stock_report_read')]:
            history = rows(); history[1][key] = value
            self.assertEqual(list(paired_receipts(history)), [])
        history = rows(); conflict = copy.deepcopy(history[0])
        conflict['tool_calls'][0]['function']['arguments'] = json.dumps({'reference': 'b' * 64})
        history.insert(1, conflict)
        self.assertEqual(list(paired_receipts(history)), [])

    def test_non_assistant_call_is_not_authority(self):
        history = rows(); history[0]['role'] = 'user'
        self.assertEqual(list(paired_receipts(history)), [])

    def test_incremental_index_matches_full_and_invalidates_projection(self):
        from plugins.finance.evidence import EvidenceIndex
        from plugins.finance.source_quotes import source_task_index
        index = EvidenceIndex(); history = rows()
        index.update(history[:1]); self.assertEqual(index.sources, [])
        index.update(history); self.assertEqual(index.sources, source_task_index(history))
        self.assertFalse(index.update(history + [{'role':'system','content':'说明'}]))
        changed = copy.deepcopy(history)
        changed[1]['content'] = json.dumps({'error':'failed'})
        self.assertTrue(index.update(changed)); self.assertEqual(index.sources, [])
