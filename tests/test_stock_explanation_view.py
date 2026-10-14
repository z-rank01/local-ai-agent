import hashlib
import json
import unittest

from core.context_manager import ContextManager
from core.stock_source_quotes import prior_explanation_view
from tests import test_stock_source_quotes


def source_turn(ref, label):
    call_id='call-'+label
    return [
        {'role':'user','content':'核对通知 '+ref},
        {'role':'assistant','content':'旧解释 '+label+'，每年目录独立审核。','tool_calls':[
            {'id':call_id,'function':{'name':'stock_public_evidence_read','arguments':{'reference':ref,'event_id':'c'*16}}}]},
        {'role':'tool','tool_name':'stock_public_evidence_read','tool_call_id':call_id,'content':json.dumps({
            'kind':'public_evidence_read','source_reference':ref,'event_id':'c'*16,'title':'通知 '+label,
            'excerpts':[{'kind':'ORIGINAL_TEXT','text':'完整原文。末尾反证：用途仍受限制。'}]},ensure_ascii=False)},
        {'role':'assistant','content':'以前的完整解释 '+label+'，末尾旧决定仍能回读。'},
    ]


class PriorExplanationViewTests(unittest.TestCase):
    def archive(self, message):
        eid=hashlib.sha256(json.dumps(message,sort_keys=True).encode()).hexdigest()
        self.saved[eid]=message
        return eid

    def setUp(self):
        self.saved={}

    def test_bound_old_prose_indexed_but_sources_calls_users_and_other_task_unchanged(self):
        messages=[{'role':'system','content':'方法正文与权限规则'}]+source_turn('a'*64,'甲')+source_turn('b'*64,'乙')+[
            {'role':'user','content':'请按通知 '+('a'*64)+' 的原文回答资金用途'}]
        before=json.dumps(messages,ensure_ascii=False)
        view,index=prior_explanation_view(messages,self.archive)
        self.assertEqual(json.dumps(messages,ensure_ascii=False),before)
        self.assertEqual(len(index),2)
        self.assertEqual(view[0],messages[0])
        self.assertEqual(view[-1],messages[-1])
        self.assertIn(messages[3],view)
        self.assertIn(messages[5],view)
        self.assertIn(messages[6],view)
        self.assertIn(messages[8],view)
        request=next(row for row in view if row.get('tool_calls') and row['tool_calls'][0]['id']=='call-甲')
        self.assertEqual(request['tool_calls'],messages[2]['tool_calls'])
        self.assertEqual(request['content'],'')
        self.assertNotIn('以前的完整解释 甲', '\n'.join(row.get('content','') for row in view))
        self.assertIn('末尾旧决定',self.saved[index[1]['evidence_id']]['content'])
        self.assertEqual(self.saved[index[0]['evidence_id']]['content'],messages[2]['content'])

    def test_unknown_reference_or_unbound_question_does_not_select_task(self):
        for question in ('请解释上个代码片段','请读未知任务 '+('d'*64)):
            messages=source_turn('a'*64,'甲')+[{'role':'user','content':question}]
            view,index=prior_explanation_view(messages,self.archive)
            self.assertEqual(view,messages)
            self.assertFalse(index)
        self.assertFalse(self.saved)

    def test_current_receipt_binds_view_without_a_hash_in_user_question(self):
        turn=source_turn('a'*64,'甲')
        current=source_turn('a'*64,'当前')
        current[0]={'role':'user','content':'请核对这份通知'}
        messages=turn+current
        view,index=prior_explanation_view(messages,self.archive)
        self.assertEqual(len(index),2)
        self.assertEqual(view[-4:],current)

    def test_archive_failure_preserves_original_input_and_does_not_make_a_view(self):
        messages=source_turn('a'*64,'甲')+[{'role':'user','content':'核对 '+('a'*64)}]
        before=json.dumps(messages)
        def failure(message):raise OSError('archive unavailable')
        with self.assertRaises(OSError):prior_explanation_view(messages,failure)
        self.assertEqual(json.dumps(messages),before)
        self.assertEqual(prior_explanation_view(messages,None),(messages,[]))

    def test_full_prior_decision_can_be_read_only_with_registered_index(self):
        messages=source_turn('a'*64,'甲')+[{'role':'user','content':'核对 '+('a'*64)}]
        _,index=prior_explanation_view(messages,self.archive)
        manager=ContextManager()
        manager.reader=lambda eid,cursor:{'content':self.saved[eid]['content'][cursor:]}
        manager.evidence_ids.update(row['evidence_id'] for row in index)
        self.assertIn('末尾旧决定',manager.read_evidence(index[1]['evidence_id'])['content'])
        with self.assertRaises(ValueError):manager.read_evidence('not-registered')

    def test_long_old_question_is_kept_once_not_copied_per_archived_answer(self):
        history=source_turn('a'*64,'甲')
        question='历史待检：未签合同所以合同效力未定。正确对照与原文各有归属。\n' * 100 + 'a'*64
        history[0]['content']=question
        messages=history+[{'role':'user','content':'核对当前公告 '+('a'*64)}]
        original=json.dumps(messages,ensure_ascii=False)
        view,index=prior_explanation_view(messages,self.archive)
        self.assertEqual(json.dumps(messages,ensure_ascii=False),original)
        self.assertIn(messages[0],view)
        self.assertEqual(sum(m.get('content','').count(question) for m in view),1)
        self.assertEqual(len(index),2)
        for row in index:
            self.assertNotIn('question',row)
            self.assertEqual(row['question_sha256'],hashlib.sha256(question.encode()).hexdigest())
            self.assertEqual(row['view_version'],'prior-explanation-index-v2')
        self.assertEqual(self.saved[index[0]['evidence_id']]['content'],messages[1]['content'])
        self.assertEqual(self.saved[index[1]['evidence_id']]['content'],messages[3]['content'])


class PriorExplanationAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_cloud_read_grant_or_answer_only_keeps_full_prior_prose(self):
        for cloud,allow_tools in ((True,True),(False,False)):
            with self.subTest(cloud=cloud,allow_tools=allow_tools):
                seen=[];archived=[]
                class Model:
                    async def chat_stream_with_tools(self,messages,tools,**options):
                        seen.extend(messages)
                        assert not tools
                        yield '原文：“用途仍受限制”。',None
                        yield '',{'role':'assistant','content':'原文：“用途仍受限制”。'}
                model=Model();model.cloud=cloud
                agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,model,object(),['stock_public_evidence_read'])
                agent.allow_tools=allow_tools;agent.workspace_cloud_allowed=False
                manager=agent.context_mgr
                manager.archive=lambda row:archived.append(row) or 'e'*64
                manager.reader=lambda eid,cursor:{'content':'stored'};manager.evidence_ids=set()
                history=source_turn('a'*64,'甲')+[{'role':'user','content':'请核对 '+('a'*64)}]
                events=[event async for event in agent.run(history)]
                self.assertEqual(events[-1].kind,'done')
                self.assertTrue(any('以前的完整解释 甲' in row.get('content','') for row in seen))
                self.assertFalse(archived)
                self.assertFalse(manager.evidence_ids)

    async def test_actual_model_view_uses_index_and_keeps_authorized_original(self):
        seen=[];saved={};audit=[]
        class Model:
            cloud=False
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools,**options):
                seen.extend(messages)
                assert 'request_observer' in options
                assert 'context_evidence_read' in [row['function']['name'] for row in tools]
                yield '原文：“用途仍受限制”。',None
                yield '',{'role':'assistant','content':'原文：“用途仍受限制”。'}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),object(),['stock_public_evidence_read'])
        manager=agent.context_mgr
        def archive(message):
            eid=hashlib.sha256(json.dumps(message,sort_keys=True).encode()).hexdigest()
            saved[eid]=message
            return eid
        manager.archive=archive;manager.reader=lambda eid,cursor: {'content':saved[eid]['content'][cursor:]}
        manager.evidence_ids=set()
        agent.audit.record=lambda event,data:audit.append((event,data))
        history=source_turn('a'*64,'甲')+[{'role':'user','content':'请核对 '+('a'*64)}]
        events=[event async for event in agent.run(history)]
        self.assertEqual(events[-1].kind,'done')
        self.assertFalse(any('每年目录独立审核' in row.get('content','') for row in seen))
        self.assertTrue(any('末尾反证：用途仍受限制' in row.get('content','') for row in seen))
        self.assertTrue(any(event=='task_explanation_view' for event,data in audit))
        self.assertTrue(any(event=='task_explanation_view' and data.get('prior_explanation_index_version')=='prior-explanation-index-v2' for event,data in audit))
        self.assertEqual(len(saved),2)

