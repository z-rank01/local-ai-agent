"""Recipe-v2 transport and publication behavior, not financial quality scores."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import config
from harness.runtime.evidence import fingerprint
from harness.runtime.request_materials import material_observer, observe_materials
from plugins.finance.research.recipe import update_recipe
from plugins.finance.research.session import ResearchSession
from plugins.finance.research_review import review_messages
from tests import test_research_session as session_tests, test_research_review as review_tests


class RecipeTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_captures_serialized_input_not_preprojection_messages(self):
        import httpx
        from core.providers import CompatibleClient, CallBudget
        from plugins.finance.loop import FinanceLoop
        _,run,_=await session_tests.ResearchSessionTests().prepare()
        saved,observed,requests={},[],[]
        manager=SimpleNamespace(evidence_ids=set())
        def archive(row):
            key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
        manager.archive=archive
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,text='data: '+json.dumps({'choices':[{
                'delta':{'content':'回答'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
        client=CompatibleClient({'id':'fixture','model':'fixture','api_key_env':'FAKE',
            'base_url':'https://example.invalid/v1'},CallBudget('unused',0))
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(client.close)
        agent=FinanceLoop(llm=client,router=SimpleNamespace(research=run),registry=None,
            audit=None,context_mgr=manager,prompt_builder=None)
        messages=[{'role':'assistant','content':'', 'reasoning_content':'PRIVATE_REASONING',
            'tool_calls':[{'id':'call','type':'function','function':{'name':'file_read','arguments':{'path':'公开文件'}}}]}]
        with patch('core.providers.read_key',return_value='SECRET_KEY'):
            _=[item async for item in agent._model_stream(messages,request_observer=lambda e,d:observed.append((e,d)))]
        native=next(d for e,d in observed if e=='native_model_request')
        manifest=native['material_manifest']
        row=saved[manifest['messages'][0]['evidence_id']]
        self.assertIsInstance(row['tool_calls'][0]['function']['arguments'],str)
        self.assertEqual(row['tool_calls'],requests[0]['messages'][0]['tool_calls'])
        self.assertNotIn('PRIVATE_REASONING',json.dumps(saved))
        self.assertNotIn('SECRET_KEY',json.dumps(native))
        self.assertEqual(manifest['local_attempt_id'],native['local_attempt_id'])

    async def test_dynamic_work_order_keeps_receipts_and_old_revision(self):
        run = ResearchSession(session_tests.Files(), conversation_id='c',model='frozen',allowed=True)
        result = await run.begin({'phase':'准备','scope':'现金流','plan':['读取原表']})
        recipe = copy.deepcopy(result['recipe'])
        offered = run.tool_names
        stage = recipe['stages'][0]['id']
        self.assertEqual(run.record['research_contract_version'],2)
        await run.observe({'name':'code_exec','status':'ok','result':{'stdout':'实际计算'}})
        result = await run.begin({'phase':'分析','stage_updates':[{'id':stage,'model_status':'partial',
            'output_refs':['receipt:1'],'open_questions':['期间仍待核实'],
            'claims':[{'text':'净额变化不能当作销售收现变化','metric':'经营现金流净额',
                       'period':'半年累计','evidence_refs':['receipt:1']}]},
            {'goal':'补充核实期间','depends_on':[stage]}]})
        self.assertEqual(len(result['recipe']['stages']),2)
        self.assertEqual(run.record['recipe_history'][0],recipe)
        self.assertEqual(result['artifacts']['receipt:1']['status'],'ok')
        before=copy.deepcopy(run.record)
        for patch_value in ({'id':stage,'status':'COMPLETE'}, {'id':stage,'output_refs':['forged']},
                            {'id':stage,'depends_on':['missing']}, {'id':stage,'claims':[{'text':'x','evidence_refs':['forged']}]}):
            with self.assertRaises(ValueError):
                await run.begin({'phase':'分析','stage_updates':[patch_value]})
            self.assertEqual(run.record,before)
        self.assertEqual(run.tool_names,offered)

    async def test_checkpoint_preserves_recipe_and_unknown_without_reexecution(self):
        _,run,_ = await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        run.review_records={'unknown':{'status':'UNKNOWN'}}
        archive={}
        def save(row):
            key=fingerprint(row);archive[key]=row;return key
        state=copy.deepcopy(run.checkpoint_state(save))
        restored=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        restored.restore_state(state,archive.__getitem__)
        self.assertEqual(restored.record['recipe'],run.record['recipe'])
        self.assertEqual(restored.review_records,run.review_records)

    async def test_actual_serialized_material_manifest_and_method_handoff(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        archive={}
        manager=SimpleNamespace(evidence_ids=set())
        def save(row):
            key=fingerprint(row);archive[key]=copy.deepcopy(row);return key
        manager.archive=save
        guidance=run.guidance()
        self.assertIn('methods',guidance)
        token=material_observer.set(lambda messages,metadata:run.capture_input(messages,metadata,manager))
        try:
            data=observe_materials([{'role':'system','content':guidance},
                {'role':'assistant','content':'visible','reasoning_content':'HIDDEN'}],{'call_id':'actual-call'})
        finally:
            material_observer.reset(token)
        manifest=data['material_manifest']
        self.assertEqual(manifest['call_id'],'actual-call')
        self.assertTrue(all(row['included'] for row in manifest['materials']))
        self.assertNotIn('HIDDEN',json.dumps(archive))
        self.assertEqual(set(archive),manager.evidence_ids)
        absent=run.capture_input([{'role':'user','content':'没有原文'}],{'call_id':'next'},manager)
        self.assertTrue(all(not row['included'] for row in absent['materials']))
        draft['review']['conflicts']=['指标可能错配']
        review=json.loads(review_messages('原问题',draft,run,'permissions')[1]['content'])
        self.assertEqual(review['known_conflicts'],['指标可能错配'])
        self.assertIn('recipe',review)

    async def test_advisory_feedback_then_explicit_publish_without_second_review(self):
        issue={'section':'结论','claim':'推断','reason':'缺口','evidence':'原文','action':'可补查','severity':'blocking'}
        files,run,draft,agent,calls=await review_tests.ReviewTests().setup_review([
            {'issues':[issue],'checks':['具体检查'], 'read_requests':[]}])
        run.record['research_contract_version']=2
        history=[{'role':'system','content':'permissions'},{'role':'user','content':'原自然问题'}]
        async def dispatch(name,args):
            events=[event async for event in agent._dispatch_tool(name,args,'c',None,'id',history)]
            return next(event.data['result'] for event in events if event.kind=='tool_result')
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            ready=await dispatch('brief_publish',draft)
            self.assertEqual(ready['status'],'REVIEW_READY')
            self.assertIsNone(run.published)
            self.assertEqual(ready['review']['issues'],[issue])
            self.assertFalse(any(path.endswith('report.html') for path in files.files))
            again=await dispatch('brief_publish',{**draft,'review_ref':'wrong','review_response':'发布'})
            self.assertEqual(again['status'],'REVIEW_READY')
            published=await dispatch('brief_publish',{**draft,'review_ref':ready['review_ref'],
                'review_response':'保留证据支持的判断，明确说明限制后发布'})
            self.assertEqual(published['status'],'PUBLISHED')
            self.assertEqual(len(calls),1)
            self.assertEqual(len(run.record['publication_decisions']),1)
            self.assertNotIn('review_response',json.loads(files.files[run.record['directory']+'/evidence.json']))

    async def test_changed_draft_invalidates_feedback_and_direct_backend_cannot_bypass(self):
        _,run,draft,agent,calls=await review_tests.ReviewTests().setup_review([
            {'issues':[],'checks':['首次核对']},{'issues':[],'checks':['新稿核对']}])
        run.record['research_contract_version']=2
        history=[{'role':'user','content':'问题'}]
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            first=[e async for e in agent._dispatch_tool('brief_review',draft,'c',None,'id',history)]
            ref=next(e.data['result']['review_ref'] for e in first if e.kind=='tool_result')
            changed={**draft,'markdown':'新的结论'}
            result=[e async for e in agent._dispatch_tool('brief_publish',
                {**changed,'review_ref':ref,'review_response':'发布'},'c',None,'id',history)]
            self.assertEqual(next(e.data['result']['status'] for e in result if e.kind=='tool_result'),'REVIEW_READY')
            self.assertEqual(len(calls),2)
            with self.assertRaisesRegex(ValueError,'review_ref'):
                await run.publish(changed)

    async def test_advisory_read_requests_return_to_author_without_automatic_execution(self):
        outputs=[]
        _,run,draft,agent,calls=await review_tests.ReviewTests().setup_review(outputs)
        run.record['research_contract_version']=2
        reads=[{'source_id':draft['sources'][0]['id'],'terms':['现金流']}]
        outputs.append({'issues':[],'checks':['需要进一步核实'],'read_requests':reads})
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            events=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'id',[{'role':'user','content':'问题'}])]
        result=next(e.data['result'] for e in events if e.kind=='tool_result')
        self.assertEqual(result['review']['read_requests'],reads)
        self.assertEqual(run.pending_evidence,[])
        self.assertIsNone(run.published)
