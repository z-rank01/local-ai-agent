import hashlib
import json
import unittest
from types import SimpleNamespace

from plugins.finance.history import project_history


class DeliveredHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = {}
        directory = '/workspace/reports/chat-research/' + 'a' * 32
        self.path = directory + '/report.html'
        self.turn = [
            {'role':'user', 'content':'研究公司'},
            *self.exchange('research_begin', 'one', {'research_id':'a'*32, 'directory':directory}),
            *self.exchange('file_read', 'two', {'content':'原值与反证'}),
            *self.exchange('brief_publish', 'three', {'status':'PUBLISHED', 'path':self.path,
                                                     'title':'正式稿', 'sha256':'b'*64}),
            {'role':'assistant','content':'已交付原报告'},
        ]
        self.question = {'role':'user','content':'刚才报告里哪些仍是未知？'}

    def exchange(self, name, key, result):
        return [{'role':'assistant', 'content':'', 'reasoning_content':'private',
                 'tool_calls':[{'id':key,'type':'function','function':{'name':name,'arguments':{'key':key}}}]},
                {'role':'tool', 'tool_call_id':key, 'tool_name':name, 'content':json.dumps(result)}]

    def archive(self, message):
        key = hashlib.sha256(json.dumps(message,sort_keys=True).encode()).hexdigest()
        self.saved[key] = message
        return key

    def test_published_turn_is_recoverable_and_current_exchange_is_untouched(self):
        tail = [self.question, *self.exchange('file_read','four',{'content':'新回执'})]
        result = project_history(self.turn + tail, self.archive)
        self.assertEqual(result.messages[0], self.turn[0])
        delivery = json.loads(result.messages[1]['content'])
        self.assertEqual(delivery['path'], self.path)
        index = json.loads(self.saved[delivery['history_index']]['content'])
        originals = [self.saved[item['evidence_id']] for item in index]
        self.assertEqual([item['tool_call_id'] for item in originals if item['role']=='tool'],
                         ['one','two','three'])
        self.assertEqual(originals[2]['tool_calls'], self.turn[3]['tool_calls'])
        self.assertEqual(originals[3]['content'], self.turn[4]['content'])
        self.assertNotIn('private', str(self.saved))
        self.assertEqual(result.evidence_ids,set(self.saved))
        self.assertEqual(result.messages[2:], [self.turn[-1], *tail])

    def test_requires_a_later_user_turn_and_a_matching_native_publication(self):
        self.assertEqual(project_history(self.turn,self.archive).messages,self.turn)
        for change in ({'tool_call_id':'unpaired'}, {'content':'{"status":"UNKNOWN"}'},
                       {'content':json.dumps({'status':'PUBLISHED','path':self.path.replace('a'*32,'c'*32),
                                             'sha256':'b'*64})}):
            history = [*self.turn[:6], {**self.turn[6], **change}, self.turn[-1], self.question]
            self.assertEqual(project_history(history,self.archive).messages,history)
        self.assertFalse(self.saved)

    def test_invalid_archive_id_cannot_replace_history(self):
        history = self.turn + [self.question]
        with self.assertRaisesRegex(ValueError,'归档'):
            project_history(history, lambda _:None)
        self.assertEqual(history[6], self.turn[6])

    async def test_host_routes_published_parent_without_direct_stock_read(self):
        from core import config
        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        from plugins.finance.loop import FinanceLoop
        registry = ToolRegistry(config.TOOLS_DIR, enabled_backends=('stock-bridge','research-runtime'))
        processed = []
        async def process(messages):
            processed.append(list(messages))
            return messages
        class Client:
            cloud = True
            async def chat_stream_with_tools(self, messages, tools=None, **kwargs):
                yield '', {'role':'assistant', 'content':'请按报告中列出的原件核对。'}
        host = Agent(llm=Client(), router=SimpleNamespace(), registry=registry,
            audit=SimpleNamespace(record=lambda *args:None),
            context_mgr=SimpleNamespace(process=process, archive=self.archive,
                reader=lambda *args:{}, evidence_ids=set()),
            prompt_builder=SimpleNamespace(build=lambda **kwargs:'test'), max_rounds=1)
        host.workspace_cloud_allowed = True
        host.original_user_request = self.question['content']
        self.assertEqual(FinanceLoop(host).original_user_request,self.question['content'])
        events = [e async for e in host.run(self.turn + [self.question])]
        self.assertEqual(events[-1].kind,'done')
        self.assertTrue(processed)
        self.assertIn('history_index', json.dumps(processed[0]))
        self.assertNotIn('原值与反证', json.dumps(processed[0],ensure_ascii=False))
        self.assertTrue(host.context_mgr.evidence_ids)
