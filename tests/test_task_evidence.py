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
        with self.assertRaisesRegex(RuntimeError,'不自动重发'):
            await manager._summarize_chunk('公开材料', target_chars=999)
        self.assertEqual(len(calls),1)

    async def test_legacy_unknown_summary_is_not_reissued_under_new_prompt(self):
        import hashlib, json
        from core.context_manager import _AUTOCOMPACT_USER_V1, _AUTOCOMPACT_SYSTEM
        class Model:
            async def chat(self, *args):
                raise AssertionError('legacy UNKNOWN must remain isolated')
        manager = ContextManager(llm=Model())
        manager.summary_profile = {'model':'a'}
        manager.archive = lambda m: self.store.archive_task_evidence(self.conv.id, 'cloud', m)
        manager.summary_cache_reader = lambda key: self.store.cached_task_summary(self.conv.id, 'cloud', key)
        key = hashlib.sha256(json.dumps({'profile':manager.summary_profile,
            'capacity':manager.context_window, 'system':_AUTOCOMPACT_SYSTEM,
            'instruction':_AUTOCOMPACT_USER_V1, 'text':'原始未决历史'},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        manager.archive({'role':'assistant','record_kind':'task_summary_call',
                         'summary_cache_key':key,'content':'{"status":"UNKNOWN"}'})
        with self.assertRaisesRegex(RuntimeError,'不自动重发'):
            await manager._summarize_chunk('原始未决历史', target_chars=1000)

    async def test_summary_target_accounts_for_preserved_turn_without_cutting_sources(self):
        import re
        instructions = []
        class Model:
            async def chat(self, system, user):
                instructions.append(user)
                return '关键反证：合同终止；需要核对现金流，来源保留。'
        async def compact(recent):
            manager = ContextManager(context_window=4096, preserve_recent=2, llm=Model())
            manager.archive = lambda m: self.store.archive_task_evidence(self.conv.id, 'local', m)
            manager.reader = lambda eid, cursor: self.store.read_task_evidence(self.conv.id, 'local', eid, cursor)
            source = '公开财报正文。' * 160 + '合同终止，不得视作订单增长。'
            messages = [{'role':'user','content':'核对收入与反证'},
                {'role':'tool','tool_call_id':'source','content':source},
                {'role':'assistant','content':'待核对'},
                {'role':'user','content':recent}, {'role':'assistant','content':'当前任务'}]
            result = await manager.auto_compact(messages)
            eid = manager.archive(messages[1])
            self.assertEqual(manager.read_evidence(eid)['content'], source)
            self.assertIn('合同终止', result[0]['content'])
            self.assertEqual(result[-2]['content'], recent)
            return int(re.search(r'约为 (\d+) 字符', instructions[-1]).group(1))
        roomy = await compact('请继续核对')
        tight = await compact('当前要求与条件。' * 200)
        self.assertLess(tight, roomy)
        self.assertTrue(all('合同终止，不得视作订单增长。' in value for value in instructions))

    async def test_expanding_summary_retains_original_and_archived_counterevidence(self):
        class Model:
            async def chat(self, system, user):
                return user + '不能压缩的额外说明。'
        manager = ContextManager(context_window=4096, preserve_recent=2, llm=Model())
        manager.archive = lambda m: self.store.archive_task_evidence(self.conv.id, 'local', m)
        manager.reader = lambda eid, cursor: self.store.read_task_evidence(self.conv.id, 'local', eid, cursor)
        messages = [{'role':'user','content':'核对合同'},
                    {'role':'tool','content':'正文。'*1500+'末尾修订：合同终止'},
                    {'role':'assistant','content':'待核验'},
                    {'role':'user','content':'继续'}, {'role':'assistant','content':'处理中'}]
        result = await manager.auto_compact(messages)
        self.assertEqual(result, messages)
        eid = manager.archive(messages[1])
        self.assertIn('合同终止', manager.read_evidence(eid)['content'])

    async def test_nonshrinking_summary_is_not_repeated_for_transient_guidance(self):
        calls=[]
        class Model:
            async def chat(self, system, user):
                calls.append(user)
                return user+'不能缩短的说明'
        manager=ContextManager(context_window=4096,preserve_recent=2,llm=Model())
        manager.archive=lambda m:self.store.archive_task_evidence(self.conv.id,'local',m)
        manager.reader=lambda eid,cursor:self.store.read_task_evidence(self.conv.id,'local',eid,cursor)
        messages=[{'role':'user','content':'核对'}, {'role':'tool','content':'原文。'*1900+'末尾：合同取消'},
                  {'role':'assistant','content':'待核'}, {'role':'user','content':'继续'}, {'role':'assistant','content':'当前问题'}]
        result=await manager.process(messages)
        self.assertEqual(result,messages)
        count=len(calls)
        self.assertGreater(count,0)
        self.assertEqual(await manager.process(messages+[{'role':'system','content':'按当前问题交付'}]),
                         messages+[{'role':'system','content':'按当前问题交付'}])
        self.assertEqual(len(calls),count)
        eid=manager.archive(messages[1])
        self.assertIn('末尾：合同取消',manager.read_evidence(eid)['content'])

    async def test_failed_compaction_blocks_oversize_send_without_retry(self):
        calls=[]
        class Model:
            async def chat(self,*args):
                calls.append(args)
                raise TimeoutError('unknown')
        manager=ContextManager(context_window=4096,preserve_recent=2,llm=Model())
        manager.summary_profile={'model':'fixed'}
        manager.archive=lambda m:self.store.archive_task_evidence(self.conv.id,'cloud',m)
        manager.reader=lambda eid,cursor:self.store.read_task_evidence(self.conv.id,'cloud',eid,cursor)
        manager.summary_cache_reader=lambda key:self.store.cached_task_summary(self.conv.id,'cloud',key)
        messages=[{'role':'user','content':'核对'}, {'role':'tool','content':'完整原文。'*6000+'末尾反证'},
                  {'role':'assistant','content':'待核'}, {'role':'user','content':'继续'}, {'role':'assistant','content':'当前问题'}]
        for extra in ([],[{'role':'system','content':'保留当前问题'}]):
            with self.assertRaisesRegex(RuntimeError,'不能发送超窗材料或重复压缩'):
                await manager.process(messages+extra)
        self.assertEqual(len(calls),1)
        eid=manager.archive(messages[1])
        self.assertIn('末尾反证',manager.read_evidence(eid,len(messages[1]['content'])-20)['content'])
