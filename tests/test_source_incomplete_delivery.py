import copy
import unittest
from core.stock_source_review import incomplete_delivery


class IncompleteDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.ref = 'a' * 64
        self.index = [{'source_reference': self.ref, 'report_read': True}]
        self.records = [{'source_reference': self.ref, 'excerpts': [{
            'kind': 'ORIGINAL_TEXT', 'excerpt_id': 'one', 'text': '原文片段',
            'url': 'https://www.ndrc.gov.cn/saved.html'}]}]

    def test_only_saved_read_navigation_no_model_claims(self):
        before = copy.deepcopy(self.records)
        text = incomplete_delivery(self.index, self.ref, self.records * 2, authorised=True)
        self.assertEqual(text.count('https://'), 1)
        self.assertIn('原报告卡', text)
        self.assertIn('不表示网站全文已读完', text)
        self.assertNotIn('原文片段', text.replace('保存原文片段', ''))
        self.assertNotIn(self.ref, text)
        self.assertEqual(before, self.records)

    def test_no_cross_task_or_unauthorised_navigation(self):
        for question, authorised, index in (
            (self.ref, False, self.index), ('b' * 64, True, self.index),
            ('自然问题', True, self.index + [{'source_reference': 'b' * 64}])):
            text = incomplete_delivery(index, question, self.records, authorised=authorised)
            self.assertNotIn('https://', text)
            self.assertNotIn('原报告卡', text)

    def test_financial_metadata_and_invalid_links_are_not_originals(self):
        for changes in ({'kind': 'SOURCE_FIELD'}, {'url': 'https://evil.test/x'},
                        {'url': 'https://www.ndrc.gov.cn/<x>'}, {'text': ''}):
            rows = copy.deepcopy(self.records); rows[0]['excerpts'][0].update(changes)
            self.assertNotIn('https://', incomplete_delivery(self.index, self.ref, rows, authorised=True))

    def test_source_find_only_does_not_claim_reading(self):
        index = [{'source_reference': self.ref, 'sources': [{'url': 'https://www.ndrc.gov.cn/x'}]}]
        self.assertNotIn('https://', incomplete_delivery(index, self.ref, [], authorised=True))

    def test_failed_read_does_not_become_retained_evidence(self):
        self.records[0]['status'] = 'FAILED'
        self.assertNotIn('https://', incomplete_delivery(self.index, self.ref, self.records, authorised=True))

