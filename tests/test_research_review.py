import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import config
from plugins.finance.loop import FinanceLoop as Agent
from plugins.finance.research_review import fingerprint, parse_review, review_messages
from harness.runtime.evidence import model_evidence
from plugins.finance.research.session import ResearchSession
from tests import test_research_session as session_tests

_enforcement = None


def setUpModule():
    """This module exercises the enforced review gate.

    Publication no longer blocks on review findings by default; these tests
    cover the opt-in ``PUBLISH_REVIEW_ENFORCE=true`` behaviour, so they pin it
    for the module rather than weakening the assertions.
    """
    global _enforcement
    _enforcement = patch.object(config, 'PUBLISH_REVIEW_ENFORCE', True)
    _enforcement.start()


def tearDownModule():
    _enforcement.stop()


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def setup_review(self, outputs):
        files, run, draft = await session_tests.ResearchSessionTests().prepare()
        del run.check_review  # Exercise production gate, not rendering-test mock.
        run.review_records.clear()
        calls=[]
        class Model:
            observes_native_requests=True
            async def chat_stream_with_tools(self, messages, tools=None, **options):
                calls.append((copy.deepcopy(messages),tools))
                value=outputs[len(calls)-1]
                observer=options['request_observer']
                receipt={'call_id':None,'local_attempt_id':'attempt-'+str(len(calls))}
                observer('native_model_request',{**receipt,'model':run.model})
                if isinstance(value,Exception):
                    observer('native_model_result',{**receipt,'result_status':'UNKNOWN'})
                    raise value
                observer('native_model_result',{**receipt,'result_status':'COMPLETE'})
                yield '',{'role':'assistant','content':json.dumps(value,ensure_ascii=False),'reasoning_content':'not saved'}
        agent=Agent(llm=Model(),router=SimpleNamespace(research=run),
            registry=None,audit=SimpleNamespace(record=lambda *args:None),context_mgr=None,prompt_builder=None)
        return files,run,draft,agent,calls

    async def review(self,agent,draft):
        history=[{'role':'system','content':'原权限与模型约定'}, {'role':'user','content':'原自然问题'},
                 {'role':'assistant','content':'此前自信地说已经全部核实','reasoning_content':'private'}]
        return [e async for e in agent._review_candidate(draft,'session',history)]

    async def test_clean_same_model_review_publish_and_dedup(self):
        files,run,draft,agent,calls=await self.setup_review([{'issues':[],'checks':['已对照原句与期间']}])
        with self.assertRaises(ValueError): run.check_review(draft)
        run.steps_without_progress=11
        run._observed_milestones=len(run._milestones)
        events=await self.review(agent,draft)
        await run.observe({'name':'research_review','status':'ok','params':{},'result':{}})
        self.assertEqual(run.steps_without_progress,0)
        self.assertEqual([e.kind for e in events],['tool_start','tool_end'])
        text=json.dumps(calls[0][0],ensure_ascii=False)
        self.assertIn('原自然问题',text)
        self.assertIn('营业收入100元',text)
        self.assertNotIn('此前自信',text)
        self.assertNotIn('private',text)
        self.assertIsNone(calls[0][1])
        published=await run.publish(draft)
        self.assertIn('report.html',published['path'])
        self.assertNotIn('not saved',' '.join(files.files.values()))
        await self.review(agent,draft)
        self.assertEqual(len(calls),1)
        self.assertFalse(run.review_records[fingerprint(draft)]['semantic_certification'])
        self.assertEqual(run.review_records[fingerprint(draft)]['native_call_id'],'attempt-1')

    async def test_issues_revision_and_changed_evidence_require_new_review(self):
        issue={'section':'理由','claim':'2025亏损所以当前仍亏损','reason':'缺当前期间',
               'evidence':'只有2025原文','action':'补查或限定历史期间','severity':'blocking'}
        files,run,draft,agent,calls=await self.setup_review([
            {'issues':[issue],'checks':['发现期间越界']}, {'issues':[],'checks':['已限定历史期间']}])
        await self.review(agent,draft)
        with self.assertRaises(ValueError): await run.publish(draft)
        revised={**draft,'markdown':'## 结论\n仅说明历史期间，不推断当前状态。'}
        with self.assertRaises(ValueError): run.check_review(revised)
        await self.review(agent,revised)
        run.check_review(revised)
        source=next(iter(run.sources))
        run.evidence_notes[source]={'note':'新取得更正，原值待核对'}
        with self.assertRaisesRegex(ValueError,'证据已变化'):run.check_review(revised)
        self.assertEqual(len(calls),2)
        self.assertEqual(sum(p.endswith('-candidate.json') for p in files.files),2)

    async def test_advisory_finding_does_not_block_publication(self):
        # A finding without severity is advice: it is recorded but the draft
        # still publishes, which is what lets a candidate converge.
        issue={'section':'措辞','claim':'标题可更简短','reason':'表达偏好',
               'evidence':'S','action':'可选修改'}
        _,run,draft,agent,calls=await self.setup_review([{'issues':[issue],'checks':['仅措辞问题']}])
        await self.review(agent,draft)
        result=await run.publish(draft)
        self.assertIn('path', result)

    async def test_unknown_does_not_publish_or_restart_with_changed_prose(self):
        files,run,draft,agent,calls=await self.setup_review([RuntimeError('timeout')])
        with self.assertRaisesRegex(RuntimeError,'timeout'): await self.review(agent,draft)
        self.assertEqual(run.review_records[fingerprint(draft)]['status'],'UNKNOWN')
        self.assertEqual(run.review_records[fingerprint(draft)]['native_call_id'],'attempt-1')
        with self.assertRaises(ValueError): await run.publish(draft)
        with self.assertRaisesRegex(RuntimeError,'未确认'): await self.review(agent,{**draft,'markdown':'新措辞'})
        self.assertEqual(len(calls),1)
        self.assertFalse(any(p.endswith('report.html') for p in files.files))

    async def test_malformed_review_and_pending_sources_cannot_pass(self):
        files,run,draft,agent,calls=await self.setup_review([{'issues':[],'checks':[]}])
        with self.assertRaises(ValueError):await self.review(agent,draft)
        self.assertEqual(run.review_records[fingerprint(draft)]['status'],'FAILED')
        with self.assertRaisesRegex(RuntimeError,'未成功'):await self.review(agent,draft)
        run.pending_evidence=[{'name':'web_fetch'}]
        with self.assertRaisesRegex(ValueError,'取证需求'):await self.review(agent,draft)
        self.assertEqual(len(calls),1)

    def test_review_contract_rejects_scores_and_keeps_conditional_reasoning_instruction(self):
        for value in ({'confidence':0.9}, {'issues':['unsupported'],'checks':['x']}):
            with self.assertRaises(ValueError):parse_review(json.dumps(value))
        run=SimpleNamespace(evidence_index=lambda:{},method_text={})
        text=review_messages('question',{},run,'permissions')[0]['content']
        self.assertIn('保留有用的明确条件分析',text)
        self.assertIn('快报',text)
        self.assertIn('EPC',text)

    def test_writer_affirmation_not_in_independent_review_and_original_unchanged(self):
        draft={'markdown':'正文','review':{'checks':['writer_self_certification'],'limitations':['known gap']}}
        before=copy.deepcopy(draft)
        run=SimpleNamespace(evidence_index=lambda:{},method_text={})
        messages=review_messages('问题',draft,run,'permissions')
        self.assertNotIn('writer_self_certification',json.dumps(messages))
        self.assertIn('known gap',json.dumps(messages))
        self.assertEqual(draft,before)

    def test_review_compaction_keeps_all_unique_counterevidence_and_identity(self):
        source={'sources':{'a':{'received':['profit100','risk: debt','profit100 and cash10','risk: debt']},
                            'b':{'received':['profit100']}}}
        before=copy.deepcopy(source);view=model_evidence(source)
        self.assertEqual(view['sources']['a']['received'],['risk: debt','profit100 and cash10'])
        self.assertEqual(view['sources']['b']['received'],['profit100'])
        self.assertEqual(source,before)

    def test_chinese_pdf_spaces_do_not_remove_conditions_or_english_boundaries(self):
        from plugins.finance.publication import literal_text
        self.assertEqual(literal_text('签订 相关正式\n合同'),literal_text('签订相关正式合同'))
        self.assertNotEqual(literal_text('not yet signed'),literal_text('notyetsigned'))
        self.assertNotEqual(literal_text('未签订合同'),literal_text('签订合同'))

    async def test_publication_authorization_denial_precedes_supplier_call(self):
        from unittest.mock import AsyncMock
        _,run,draft,agent,calls=await self.setup_review([])
        agent.router.authorize_publication=AsyncMock(side_effect=PermissionError('grant revoked'))
        call={'id':'blocked','function':{'name':'brief_publish','arguments':draft}}
        events=[e async for e in agent._execute_tools([call],'s',[],allowed={'brief_publish'})]
        self.assertEqual(calls,[])
        self.assertEqual(events[-1].data['status'],'error')
        self.assertIn('grant revoked',events[-1].data['result']['error'])
        self.assertFalse(run.review_records)

    async def test_unknown_review_stops_before_another_planning_request(self):
        _,run,_,agent,calls=await self.setup_review([])
        run.review_records['old']={'status':'UNKNOWN'}
        async def process(messages):return messages
        agent.registry=SimpleNamespace(get_definitions=lambda **kw:[])
        agent.context_mgr=SimpleNamespace(process=process)
        agent.prompt_builder=SimpleNamespace(build=lambda **kw:'system')
        events=[e async for e in agent.run([{'role':'user','content':'原问题'}])]
        self.assertEqual(calls,[])
        self.assertEqual(events[-1].kind,'error')
        self.assertIn('不自动重发',events[-1].text)
