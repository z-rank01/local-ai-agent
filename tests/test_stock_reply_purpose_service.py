import json
from unittest.mock import patch

from bff.schemas import ChatRequest
from tests import test_in1
from tests.test_stock_reply_purpose import history, REF


class PurposeProtocolServiceTests(test_in1.ServiceTests):
    async def test_choice_protocol_saved_locally_without_rendering_as_assistant_answer(self):
        conv=self.runtime.store.create_conversation(model='qwen:qwen3.5-flash')
        for row in history()[:-1]:
            if row['role']=='system':continue
            if row['role']=='user':self.runtime.store.add_message(conv.id,role='user',content=row['content'])
            else:self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':row})
        class Bridge:
            def set_turn(self,*args,**kwargs):pass
            def has_open_turn(self,*args):return False
            async def finish_turn(self,*args):return []
            async def close(self):pass
        self.runtime.tool_registry.turn_context = {'stock-bridge': Bridge()};seen=[]
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,messages,tools=None,**kwargs):
                seen.append(messages)
                text='{"reply_focus":"analysis"}' if len(seen)==1 else '保留原文与条件分析。'
                yield text,None;yield '',{'role':'assistant','content':text}
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        self.runtime.tool_registry.load_backend('config/tools', 'stock-bridge')
        defs=[{'type':'function','function':{'name':'stock_report_read','parameters':{'type':'object'}}}]
        with patch.object(self.runtime.tool_registry,'get_definitions',return_value=defs):
            events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
                message='核对当前资料 '+REF,provider_id='qwen',model='qwen3.5-flash'))]
        self.assertEqual(len(seen),2)
        self.assertEqual([e.data['text'] for e in events if e.event=='assistant.completed'],['保留原文与条件分析。'])
        self.assertNotIn('reply_focus',''.join(e.data.get('text','') for e in events if e.event=='assistant.delta'))
        rows=[json.loads(v.metadata or '{}') for v in self.runtime.store.get_messages(conv.id)]
        protocols=[v for v in rows if v.get('reply_purpose_view_version')]
        self.assertEqual(len(protocols),1);self.assertFalse(protocols[0]['cloud_safe'])
        self.assertEqual(protocols[0]['message']['content'],'{"reply_focus":"analysis"}')
