import unittest
from scripts.quality_usage import summarize_calls


class QualityUsageTests(unittest.TestCase):
    def row(self, cid, status='SUCCEEDED', batch='b', usage=None, elapsed=2):
        return {'id': cid, 'status': status, 'body': {'batch': batch, 'sample': 'sample',
                'request_id': 'same-logical-request', 'elapsed_seconds': elapsed,
                'reply': {'usage': usage}}}

    def test_all_attempts_include_repair_and_unavailable_usage(self):
        rows = [self.row('first', 'FAILED', usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}),
                self.row('repair', usage={'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}),
                self.row('unknown', 'UNKNOWN', elapsed=None), self.row('other', batch='different')]
        report = summarize_calls(rows, 'b')
        self.assertEqual(report['attempt_count'], 3)
        self.assertEqual(report['reported_usage_sum']['total_tokens'], 35)
        self.assertEqual(report['usage_unavailable_call_ids'], ['unknown'])
        self.assertFalse(report['usage_complete'])
        self.assertEqual(report['known_failure_fraction'], 1/3)
        self.assertEqual(report['known_attempt_seconds_sum'], 4)
        self.assertFalse(report['quality_gate_affected'])

    def test_missing_and_bad_metadata_never_becomes_zero_usage(self):
        for usage in (None, {}, {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3}):
            report = summarize_calls([self.row('one', usage=usage)], 'b')
            self.assertEqual(report['usage_reported_calls'], 0)
            self.assertFalse(report['usage_complete'])

    def test_duplicates_and_empty_batch(self):
        with self.assertRaises(ValueError): summarize_calls([self.row('a'), self.row('a')], 'b')
        report = summarize_calls([], 'b')
        self.assertIsNone(report['known_failure_fraction'])
        self.assertFalse(report['usage_complete'])


if __name__ == '__main__': unittest.main()
