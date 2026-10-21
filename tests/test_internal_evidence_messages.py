"""Execution protocol is retained without creating a user-facing answer."""
import json
import unittest
from unittest.mock import patch

from bff.schemas import ChatRequest
from core.agent import Agent, AgentEvent
from tests import test_in1


class InternalEvidenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    async def test_internal_expansion_stays_in_protocol_and_tool_trace(self):
        text = '程序执行已登记证据需求：读取实际公告'
        protocol = {'role':'assistant', 'content':text, 'tool_calls':[
            {'id':'evidence-call', 'function':{'name':'web_fetch','arguments':'{}'}}]}
        async def run(*args, **kwargs):
            yield AgentEvent('message', data={'internal':True, 'message':protocol})
            yield AgentEvent('tool_start', data={'name':'web_fetch','call_id':'evidence-call',
                'params':{},'purpose':'读取实际公告'})
            yield AgentEvent('tool_end', text='已完成', data={'name':'web_fetch',
                'call_id':'evidence-call','params':{},'status':'ok','result':{},
                'purpose':'读取实际公告','result_preview':'实际原文'})
            yield AgentEvent('message',data={'message':{'role':'assistant','content':'面向用户的结论'}})
        with patch.object(Agent,'run',run):
            events=[e async for e in self.service.stream_chat(ChatRequest(
                message='研究公告', model='qwen:qwen3.5-flash'))]
        self.assertEqual([e.data['text'] for e in events if e.event=='assistant.completed'],['面向用户的结论'])
        self.assertEqual(next(e for e in events if e.event=='tool.started').data['summary'],'读取实际公告')
        cid=events[0].conversation_id
        rows=self.runtime.store.get_messages(cid)
        self.assertTrue(any(r.role=='protocol' and json.loads(r.metadata).get('message')==protocol for r in rows))
        visible=self.service.get_messages(cid)
        self.assertEqual([r.content for r in visible if r.role=='assistant'],['面向用户的结论'])
        self.assertIn('读取实际公告',next(r.content for r in visible if r.role=='tool'))

    async def test_legacy_duplicate_is_hidden_only_with_exact_adjacent_tool_protocol(self):
        conv=self.runtime.store.create_conversation()
        text='程序执行已登记证据需求：读取原文'
        store=self.runtime.store
        store.add_message(conv.id,role='protocol',metadata={'message':{'role':'assistant',
            'content':text,'tool_calls':[{'id':'old-call','function':{'name':'web_fetch'}}]}})
        duplicate=store.add_message(conv.id,role='assistant',content=text)
        ordinary=store.add_message(conv.id,role='assistant',content=text)
        store.add_message(conv.id,role='protocol',metadata={'message':{'role':'assistant',
            'content':text+'不同', 'tool_calls':[{'id':'other'}]}})
        different=store.add_message(conv.id,role='assistant',content=text)
        before=[(r.id,r.content,r.metadata) for r in store.get_messages(conv.id)]
        ids=[r.id for r in self.service.get_messages(conv.id)]
        self.assertNotIn(duplicate.id,ids)
        self.assertIn(ordinary.id,ids)
        self.assertIn(different.id,ids)
        self.assertEqual(before,[(r.id,r.content,r.metadata) for r in store.get_messages(conv.id)])
