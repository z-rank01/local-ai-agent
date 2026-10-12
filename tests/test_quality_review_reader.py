import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.quality_review import digest
from scripts.quality_review_reader import READER_INSTRUCTION, locate_reading, terminal_reading, run_reading
from scripts.quality_review_windows import plan_windows, FINDING_GROUPS, collect_readings


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'blind_id': 'b'*32, 'research_context': {'as_of': '2026-10-12'},
                       'material': {'question': '订单是否有效', 'text': '公告：原订单已经取消。'},
                       'answer': {'report': '订单仍有效'}}
        self.plan = plan_windows(self.packet, context_window=10000, output_reserve=1000,
                                 system_reserve=500, instruction=READER_INSTRUCTION)
        self.node = self.plan['windows'][0]
        self.result = {'coverage_note': '末尾反证影响原判断', 'findings': {k: [] for k in FINDING_GROUPS}}
        self.result['findings']['counterevidence'] = [{'claim': '订单仍有效', 'reason': '公告明确撤销',
            'source_spans': [{'quote': '公告：原订单已经取消。'}]}]

    def events(self):
        return [{'event': 'assistant.completed', 'message_id': 'm1',
                 'data': {'model': 'qwen:test', 'text': json.dumps(self.result)}},
                {'event': 'session.completed'}]

    def test_quotes_are_located_without_model_character_arithmetic(self):
        result = locate_reading(self.node, self.result)
        span = result['findings']['counterevidence'][0]['source_spans'][0]
        self.assertEqual(self.node['request']['source'][span['start']:span['end']], span['quote'])
        self.result['findings']['counterevidence'][0]['source_spans'][0]['quote'] = '不存在'
        with self.assertRaises(ValueError): locate_reading(self.node, self.result)

    def test_bff_receipt_is_not_fabricated_provider_metadata(self):
        receipt = terminal_reading(self.events(), self.node, 'qwen:test')
        self.assertNotIn('finish_reason', receipt)
        self.assertNotIn('response_id', receipt)
        self.assertEqual(collect_readings(self.plan, self.packet, [receipt])['status'], 'READY_FOR_SYNTHESIS')
        for extra in ({'event': 'error'}, {'event': 'tool.started'}):
            with self.assertRaises(ValueError): terminal_reading([extra]+self.events(), self.node, 'qwen:test')

    def test_reserved_unknown_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root/self.node['request_hash'];folder.mkdir()
            binding = {'request_hash': self.node['request_hash'], 'model': 'qwen:test',
                       'instruction_hash': digest(READER_INSTRUCTION), 'protocol': 'source-read-v1'}
            (folder/'binding.json').write_text(json.dumps(binding), encoding='utf-8')
            client = Mock()
            self.assertEqual(run_reading(client, self.node, root, 'qwen:test')['status'], 'UNKNOWN')
            self.assertFalse(client.mock_calls)

    def test_completed_raw_stream_recovered_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/self.node['request_hash'];folder.mkdir()
            binding={'request_hash':self.node['request_hash'],'model':'qwen:test',
                     'instruction_hash':digest(READER_INSTRUCTION),'protocol':'source-read-v1'}
            (folder/'binding.json').write_text(json.dumps(binding),encoding='utf-8')
            (folder/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in self.events()),encoding='utf-8')
            client=Mock(); receipt=run_reading(client,self.node,root,'qwen:test')
            self.assertEqual(receipt['status'],'SUCCEEDED');self.assertFalse(client.mock_calls)
            self.assertEqual(run_reading(client,self.node,root,'qwen:test'),receipt)


if __name__ == '__main__':unittest.main()
