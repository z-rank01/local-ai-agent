import copy
import json
from pathlib import Path
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
    def test_cited_page_context_is_bound_to_actually_received_page(self):
        from plugins.finance.research.material_view import cited_page_context
        body = '披露期间为前三季度。拟分红金额见本段。尚待股东会审议。'
        quote = '拟分红金额见本段。'
        start = body.index(quote)
        loc = {'page':1, 'start':start, 'end':start+len(quote)}
        run = SimpleNamespace(sources={'S1':{'sha256':'digest'}},
            excerpts={'S1':[{**loc,'quote':quote,'sha256':'digest'}]},
            _source_pages={'S1':[{'page':1,'text':body}]}, read_text={'S1':[quote]})
        citation = {'id':'S1','quote':quote,'excerpt':loc}
        candidate = {'sources':[citation, citation]}
        self.assertEqual(cited_page_context(run, candidate), [])
        run.read_text['S1'] = ['[第 1 页]\n' + body]
        self.assertEqual(cited_page_context(run, candidate), [
            {'source_id':'S1','sha256':'digest','page':1,'start':0,'end':len(body),'text':body}])
        plain = {'sources':[{'id':'S1','quote':'分红金额'}]}
        self.assertEqual(cited_page_context(run, plain), cited_page_context(run, candidate))
        run.excerpts['S1'].append({**run.excerpts['S1'][0], 'page':2})
        self.assertEqual(cited_page_context(run, plain), [])
        run.sources['S1']['sha256'] = 'changed'
        self.assertEqual(cited_page_context(run, candidate), [])

    async def test_verified_json_mode_is_review_only_and_actual_parameters_are_saved(self):
        import httpx
        from core.providers import CompatibleClient, CallBudget
        _,run,draft,agent,_=await self.setup_review([])
        spec={'id':'fixture:json','model':'same-model','api_key_env':'FAKE','base_url':'https://example.invalid',
              'structured_output':{'mode':'json_object'},'generation_parameters':{'max_tokens':393216},
              'options':{'thinking':{'type':'enabled'},'reasoning_effort':'high'}}
        client=CompatibleClient(spec,CallBudget('unused',0));await client._client.aclose()
        payloads=[]
        read_timeouts=[]
        def handler(request):
            payloads.append(json.loads(request.content))
            read_timeouts.append(request.extensions['timeout']['read'])
            body=json.dumps({'issues':[],'checks':['已对照原文']},ensure_ascii=False)
            chunk={'choices':[{'delta':{'content':body},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler));self.addAsyncCleanup(client.close)
        agent.llm=client
        with patch('core.providers.read_key',return_value='not archived'):
            await self.review(agent,draft)
            _=[item async for item in client.chat_stream_with_tools([{'role':'user','content':'普通回答'}])]
        self.assertEqual(payloads[0]['response_format'],{'type':'json_object'})
        self.assertNotIn('response_format',payloads[1])
        self.assertEqual(read_timeouts,[300.0,client._client.timeout.read])
        for payload in payloads:
            self.assertEqual(payload['model'],'same-model')
            self.assertEqual(payload['max_tokens'],393216)
            self.assertEqual(payload['thinking'],{'type':'enabled'})
            self.assertEqual(payload['reasoning_effort'],'high')
        saved=run.review_records[fingerprint(draft)]
        self.assertEqual(saved['read_idle_timeout_seconds'],300.0)
        self.assertEqual(saved['request_parameters']['response_format'],{'type':'json_object'})
        self.assertEqual(saved['native_receipt']['result_status'],'COMPLETE')
        self.assertNotIn('response_format',client.spec['options'])

    async def test_complete_but_malformed_review_keeps_parse_location_without_retry(self):
        files,run,draft,agent,calls=await self.setup_review([])
        class Malformed:
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools=None,**options):
                calls.append(messages)
                options['request_observer']('native_model_result',{
                    'local_attempt_id':'completed-invalid-json','result_status':'COMPLETE','finish_reason':'stop'})
                yield '',{'content':'{"issues": [], "checks": ["原文中的"引号"未转义"]}'}
        agent.llm=Malformed()
        with self.assertRaises(json.JSONDecodeError):await self.review(agent,draft)
        saved=run.review_records[fingerprint(draft)]
        self.assertEqual(saved['status'],'FAILED')
        self.assertEqual(saved['native_receipt']['result_status'],'COMPLETE')
        self.assertEqual(saved['failure_detail']['stage'],'review_json_parse')
        self.assertGreater(saved['failure_detail']['offset'],0)
        self.assertTrue(any(path.endswith('-response.txt') for path in files.files))
        with self.assertRaisesRegex(RuntimeError,'未成功'):await self.review(agent,draft)
        self.assertEqual(len(calls),1)
        self.assertFalse(any(path.endswith('report.html') for path in files.files))

    async def test_native_output_truncation_keeps_real_receipt_without_retry(self):
        files,run,draft,agent,calls=await self.setup_review([])
        class Truncated:
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools=None,**options):
                calls.append(messages)
                options['request_observer']('native_model_result',{
                    'local_attempt_id':'real-length','result_status':'TRUNCATED','finish_reason':'length',
                    'failure_category':'OUTPUT_TRUNCATED','usage':{'completion_tokens':65535},
                    'reasoning_content':'never archive hidden reasoning'})
                raise RuntimeError('output truncated')
                yield
        agent.llm=Truncated()
        with self.assertRaisesRegex(RuntimeError,'truncated'):
            await self.review(agent,draft)
        saved=run.review_records[fingerprint(draft)]
        self.assertEqual(saved['status'],'TRUNCATED')
        self.assertEqual(saved['native_receipt']['finish_reason'],'length')
        self.assertEqual(saved['native_receipt']['usage']['completion_tokens'],65535)
        self.assertNotIn('reasoning_content',saved['native_receipt'])
        with self.assertRaisesRegex(RuntimeError,'未成功'):
            await self.review(agent,draft)
        with self.assertRaises(ValueError):await run.publish(draft)
        self.assertEqual(len(calls),1)
        self.assertFalse(any(p.endswith('report.html') for p in files.files))

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

    def test_previous_findings_only_carry_core_questions(self):
        from plugins.finance.research_review import previous_findings
        records={'latest':{'status':'COMPLETE','attempts':['reviews/2'],'issues':[
            {'section':'结论','claim':'核心问题','evidence':'原文','category':'core_answer'},
            {'section':'补充','claim':'可选拓展','evidence':'原文','category':'optional'},
            {'section':'报告卡','claim':'发布后检查','evidence':'原文','category':'postpublication'}]}}
        self.assertEqual(previous_findings(records),[
            {'section':'结论','claim':'核心问题','evidence':'原文'}])

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

    def test_review_role_isolated_from_author_workflow_with_materials_preserved(self):
        writer=(Path(__file__).resolve().parents[1]/'plugins/finance/prompts/research.txt').read_text(encoding='utf-8')
        source={'sources':{'source-1':{'received':['主体甲；2026年三季度；单位元；利润100']}}}
        methods={'method-1':'比较时须保留主体、期间和单位'}
        run=SimpleNamespace(evidence_index=lambda:source,method_text=methods,
                            record={'recipe':{'goal':'解释利润变化'}})
        draft={'markdown':'利润100','review':{'checks':['作者自认通过'],
               'conflicts':['原表主体存在冲突'],'limitations':['未取得现金流附注']}}
        messages=review_messages('截至指定日期利润变化说明什么？',draft,run,writer)
        system=messages[0]['content'];payload=json.loads(messages[1]['content'])
        self.assertNotIn(writer,system)
        self.assertNotIn('正文结构（brief_publish.markdown',system)
        self.assertIn('单独的投研稿件核对',system)
        self.assertEqual(payload['question'],'截至指定日期利润变化说明什么？')
        self.assertEqual(payload['methods'],methods)
        self.assertEqual(payload['evidence'],model_evidence(source))
        self.assertEqual(payload['known_conflicts'],['原表主体存在冲突'])
        self.assertEqual(payload['limitations'],['未取得现金流附注'])
        self.assertNotIn('作者自认通过',json.dumps(payload,ensure_ascii=False))

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
