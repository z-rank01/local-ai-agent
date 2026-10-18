import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bff.schemas import ChatRequest
from bff.service import ChatSessionService
from core.run_checkpoint import restore_research
from tests import test_in1


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_in1.ServiceTests.asyncSetUp(self)
        self.tool_entered = asyncio.Event()
        self.tool_release = asyncio.Event()
        self.provider_calls = 0
        self.tool_calls = 0
        parent = self
        class Model:
            cloud = True
            model = 'qwen3.5-flash'
            async def chat_stream_with_tools(self,messages,tools=None,**kwargs):
                parent.provider_calls += 1
                parent.seen.append(messages)
                if parent.provider_calls == 1:
                    yield '',{'role':'assistant','content':'','reasoning_content':'synthetic-hidden',
                        'tool_calls':[{'id':'original-read','type':'function','function':{
                            'name':'file_read','arguments':'{"path":"/workspace/source.txt"}'}}]}
                else:
                    yield '',{'role':'assistant','content':'已基于原回执继续。'}
            async def close(self): pass
        self.runtime.models.clients['qwen:qwen3.5-flash'] = Model()
        async def tool(name,params,session):
            self.tool_calls += 1
            self.tool_entered.set()
            await self.tool_release.wait()
            yield {'event':'result','result':{'content':'actual original evidence','sha256':'1'*64}}
        self.runtime.router.dispatch_stream = tool

    async def pause_after_read(self):
        stream=self.service.stream_chat(ChatRequest(message='读取 source 然后回答',model='qwen:qwen3.5-flash'))
        first=await anext(stream)
        await self.tool_entered.wait()
        await self.service.control_run(first.run_id,'pause')
        self.tool_release.set()
        for _ in range(100):
            if self.service.runs.store.get(first.run_id)['status']=='PAUSED':break
            await asyncio.sleep(0.01)
        self.assertEqual(self.service.runs.store.get(first.run_id)['status'],'PAUSED')
        await stream.aclose()
        return first.run_id, first.conversation_id

    async def test_pause_resume_in_same_process_does_not_repeat_read(self):
        run,_=await self.pause_after_read()
        self.assertEqual(self.provider_calls,1)
        await self.service.control_run(run,'resume')
        _=[e async for e in self.service.runs.subscribe(run)]
        self.assertEqual((self.provider_calls,self.tool_calls),(2,1))
        self.assertEqual(self.service.runs.store.get(run)['status'],'SUCCEEDED')

    async def test_restart_manual_continuation_keeps_parent_and_tool_evidence(self):
        run,conversation=await self.pause_after_read()
        checkpoint=self.service.runs.store.get(run)['checkpoint']
        self.assertTrue(checkpoint['safe'])
        self.assertNotIn('synthetic-hidden',json.dumps(checkpoint))
        with self.runtime.store._connect() as db:
            archived=''.join(r[0] for r in db.execute('SELECT metadata FROM task_evidence'))
        self.assertNotIn('synthetic-hidden',archived)
        await self.service.control_run(run,'stop')
        restored=ChatSessionService(self.runtime)
        self.addAsyncCleanup(restored.runs.close)
        child,duplicate=await asyncio.gather(restored.control_run(run,'resume'),restored.control_run(run,'resume'))
        self.assertEqual(child['id'],duplicate['id'])
        _=[e async for e in restored.runs.subscribe(child['id'])]
        self.assertEqual(child['resume_of'],run)
        self.assertEqual(restored.runs.store.get(run)['status'],'STOPPED')
        self.assertEqual(restored.runs.store.get(child['id'])['status'],'SUCCEEDED')
        self.assertEqual(self.tool_calls,1)
        self.assertIn('actual original evidence',json.dumps(self.seen[-1],ensure_ascii=False))
        self.assertFalse(any(m['role']=='tool' or m.get('tool_calls') for m in self.seen[-1]))
        again=await restored.control_run(run,'resume')
        self.assertEqual(again['id'],child['id'])
        self.assertEqual(len([m for m in restored.get_messages(conversation) if m.role=='user']),1)

    async def test_revoked_grant_or_missing_evidence_blocks_resume(self):
        run,_=await self.pause_after_read()
        row=self.service.runs.store.get(run)
        with patch('core.run_checkpoint.environment',return_value={}):
            with self.assertRaisesRegex(Exception,'配置已变化'):
                await self.service.control_run(run,'resume')
        with self.runtime.store._connect() as db:
            db.execute('DELETE FROM task_evidence WHERE id=?',(row['checkpoint']['messages'][0],))
        with self.assertRaisesRegex(Exception,'证据不存在'):
            await self.service.control_run(run,'resume')
        self.assertEqual(self.provider_calls,1)

    async def test_stop_during_model_request_is_not_resumable(self):
        entered=asyncio.Event()
        async def pending(*args,**kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield '',None
        self.runtime.models.clients['qwen:qwen3.5-flash'].chat_stream_with_tools=pending
        stream=self.service.stream_chat(ChatRequest(message='pending',model='qwen:qwen3.5-flash'))
        first=await anext(stream)
        await entered.wait()
        await self.service.control_run(first.run_id,'stop')
        await stream.aclose()
        row=self.service.runs.store.get(first.run_id)
        self.assertFalse(self.service.runs.public(row)['resumable'])
        with self.assertRaisesRegex(Exception,'尚无确认终态'):
            await self.service.control_run(first.run_id,'resume')

    def test_read_provenance_restore_keeps_only_actually_received_segments(self):
        research=SimpleNamespace()
        state={'record':{'id':'research','status':'IN_PROGRESS'},'sources':{'S1':{'page_count':100}},
            'trace':[],'draft_count':2,'publication_attempts':1,'methods':['method.md'],
            'read_text':{'S1':['actually-read-page']}}
        restore_research(research,state,lambda eid:{'content':'only page one'})
        self.assertEqual(research.read_text,{'S1':['only page one']})
        self.assertEqual(research.sources['S1']['page_count'],100)
