import tempfile
import unittest
from pathlib import Path
from core.conversation_store import ConversationStore
from core.context_manager import ContextManager


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(Path(self.temp.name)/'conversations.db')
        self.conv = self.store.create_conversation()

    async def test_tail_counterevidence_is_summarized_and_pageable(self):
        seen = []
        class Model:
            async def chat(self, system, user):
                seen.append(user)
                return '存在合同终止反证' if '合同已终止' in user else '公开背景，待核查'
        manager = ContextManager(context_window=4096, preserve_recent=2, llm=Model())
        manager.archive = lambda m: self.store.archive_task_evidence(self.conv.id, 'local', m)
        manager.reader = lambda eid, cursor: self.store.read_task_evidence(self.conv.id, 'local', eid, cursor)
        body = '完整公开原文。'*12000+'关键反证：合同已终止'
        messages = [{'role':'system','content':'sys'}, {'role':'user','content':'核对合同'},
            {'role':'tool','tool_call_id':'t1','content':body}, {'role':'assistant','content':'待综合'},
            {'role':'user','content':'继续'}, {'role':'assistant','content':'正在核对'}]
        result = await manager.auto_compact(messages)
        self.assertTrue(any('合同已终止' in text for text in seen))
        self.assertIn('合同终止反证',result[1]['content'])
        eid = manager.archive(messages[2])
        self.assertIn('合同已终止', manager.read_evidence(eid,len(body)-30)['content'])
        self.assertEqual(messages[2]['content'],body)
        with self.assertRaises(ValueError): manager.read_evidence('other-id')

    async def test_scope_and_conversation_isolation(self):
        eid = self.store.archive_task_evidence(self.conv.id,'local',{'role':'tool','content':'SECRET'})
        with self.assertRaises(ValueError): self.store.read_task_evidence(self.conv.id,'cloud',eid)
        other = self.store.create_conversation()
        with self.assertRaises(ValueError): self.store.read_task_evidence(other.id,'local',eid)
        self.assertEqual(eid,self.store.archive_task_evidence(self.conv.id,'local',{'role':'tool','content':'SECRET'}))

    async def test_no_archive_preserves_originals(self):
        manager = ContextManager(context_window=50)
        messages = [{'role':'tool','content':'x'*1000+'TAIL'}]*10
        self.assertEqual(await manager.process(messages),messages)

    async def test_completed_chunks_reused_only_for_exact_model_and_content(self):
        calls=[]
        class Model:
            async def chat(self, system, user):
                calls.append(user)
                return '尾部反证：合同已终止' if '合同已终止' in user else '公开背景'
        def manager(profile='model-a', scope='cloud'):
            result=ContextManager(context_window=4096, preserve_recent=2, llm=Model())
            result.summary_profile={'model':profile}
            result.archive=lambda m:self.store.archive_task_evidence(self.conv.id,scope,m)
            result.reader=lambda eid,cursor:self.store.read_task_evidence(self.conv.id,scope,eid,cursor)
            result.summary_cache_reader=lambda key:self.store.cached_task_summary(self.conv.id,scope,key)
            return result
        chunk='公开背景'*5000+'合同已终止'
        first=manager()
        self.assertIn('合同已终止',await first._summarize_chunk(chunk))
        restored=manager()
        self.assertIn('合同已终止',await restored._summarize_chunk(chunk))
        self.assertEqual(len(calls),1)
        self.assertTrue(restored.evidence_ids)
        await manager('model-b')._summarize_chunk(chunk)
        await manager(scope='local')._summarize_chunk(chunk)
        await restored._summarize_chunk(chunk+'后续修订')
        self.assertEqual(len(calls),4)

    async def test_failed_summary_does_not_create_completed_cache(self):
        calls=[]
        class Model:
            async def chat(self,*args):
                calls.append(args)
                raise TimeoutError('unknown')
        manager=ContextManager(llm=Model())
        manager.summary_profile={'model':'a'}
        manager.archive=lambda m:self.store.archive_task_evidence(self.conv.id,'cloud',m)
        manager.summary_cache_reader=lambda key:self.store.cached_task_summary(self.conv.id,'cloud',key)
        with self.assertRaises(TimeoutError):
            await manager._summarize_chunk('公开材料')
        self.assertFalse(manager.evidence_ids)
        with self.assertRaisesRegex(RuntimeError,'不自动重发'):
            await manager._summarize_chunk('公开材料')
        self.assertEqual(len(calls),1)
