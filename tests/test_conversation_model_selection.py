import asyncio
import unittest
from fastapi import HTTPException
from bff.schemas import ChatRequest
from tests import test_in1


class ConversationModelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    async def test_selection_persists_without_a_turn_and_is_per_conversation(self):
        a=self.service.create_conversation('A','qwen:qwen3.5-flash')
        b=self.service.create_conversation('B','qwen:qwen3.5-flash')
        chosen=self.service.update_conversation(a.id,model='ollama:gemma4:26b')
        self.assertEqual(chosen.model,'ollama:gemma4:26b')
        self.assertEqual(self.service.get_conversation(b.id).model,'qwen:qwen3.5-flash')
        self.assertEqual(self.service.get_messages(a.id),[])

    async def test_stale_request_does_not_change_selection_or_call_model(self):
        conv=self.service.create_conversation('A','ollama:gemma4:26b')
        with self.assertRaises(HTTPException) as raised:
            _=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
                message='test',model='qwen:qwen3.5-flash'))]
        self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(self.seen,[])
        self.assertEqual(self.service.get_conversation(conv.id).model,'ollama:gemma4:26b')

    async def test_active_run_keeps_frozen_model_when_selection_changes(self):
        entered,release=asyncio.Event(),asyncio.Event()
        class Model:
            cloud=True
            async def close(self): pass
            async def chat_stream_with_tools(self,messages,tools=None):
                entered.set();await release.wait()
                yield 'hello',None
                yield '',{'role':'assistant','content':'hello'}
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        conv=self.service.create_conversation('A','qwen:qwen3.5-flash')
        async def consume():
            return [e async for e in self.service.stream_chat(ChatRequest(
                conversation_id=conv.id,message='test',model=conv.model))]
        task=asyncio.create_task(consume());await asyncio.wait_for(entered.wait(),5)
        self.service.update_conversation(conv.id,model='ollama:gemma4:26b')
        release.set();events=await task
        self.assertEqual(next(e for e in events if e.event=='model.selected').data['model'],'qwen:qwen3.5-flash')
        self.assertEqual(self.service.get_conversation(conv.id).model,'ollama:gemma4:26b')
