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
    async def test_new_research_can_register_evidence_without_reading_methods(self):
        run=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        await run.begin({'phase':'准备','scope':'公开资料','plan':['取证']})
        await run.begin({'phase':'获取','evidence_requests':[{'kind':'profile','symbol':'SH688178'}]})
        self.assertFalse(run.methods)
        self.assertTrue(run.pending_evidence)
        self.assertIn('web_fetch',run.tool_names)
        run.record['research_contract_version']=1
        with self.assertRaisesRegex(ValueError,'方法'):
            await run.begin({'phase':'获取','evidence_requests':[{'kind':'profile','symbol':'SH688178'}]})

    async def test_new_research_review_and_publish_do_not_require_method_selection(self):
        _,run,draft,agent,_=await review_tests.ReviewTests().setup_review([{'issues':[],'checks':['核对原文']}])
        run.record['research_contract_version']=2
        run.methods.clear();run.method_text.clear();run.method_fingerprints.clear()
        history=[{'role':'user','content':'公开研究'}]
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            events=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'id',history)]
            ready=next(e.data['result'] for e in events if e.kind=='tool_result')
            self.assertEqual(ready['status'],'REVIEW_READY')
            events=[e async for e in agent._dispatch_tool('brief_publish',{**draft,
                'review_ref':ready['review_ref'],'review_response':'以已取得原文为依据发布'},'c',None,'id',history)]
        self.assertEqual(next(e.data['result']['status'] for e in events if e.kind=='tool_result'),'PUBLISHED')

    async def test_author_feedback_uses_attempt_order_after_same_candidate_is_revisited(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        run.review_records={'revisited':{'status':'COMPLETE','feedback_returned':True,'attempts':['reviews/9'],
                                        'checks':['最新反馈']},
                            'older':{'status':'COMPLETE','feedback_returned':True,'attempts':['reviews/3'],
                                     'checks':['旧反馈']}}
        saved={}
        def archive(value):
            key=fingerprint(value);saved[key]=value;return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        view=await run.author_messages([{'role':'system','content':'权限'},{'role':'user','content':'问题'}],'问题',manager)
        self.assertEqual(json.loads(view[1]['content'])['latest_feedback']['checks'],['最新反馈'])

    async def test_author_packet_reloads_working_draft_and_preserves_complete_history(self):
        files,run,draft,agent,_=await review_tests.ReviewTests().setup_review([
            {'issues':[],'checks':['核对当前稿']}])
        run.record['research_contract_version']=2
        path=run.record['directory']+'/draft.json'
        files.files[path]=json.dumps(draft,ensure_ascii=False)
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            _=[e async for e in agent._dispatch_tool('brief_publish',{'draft_path':path},'c',None,'id',
                [{'role':'system','content':'原权限'},{'role':'user','content':'原问题'}])]
        edited={**draft,'markdown':'当前修订全文：仅说明公告日状态，当前状态未知。'}
        files.files[path]=json.dumps(edited,ensure_ascii=False)
        saved={}
        def archive(row):
            key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        history=[{'role':'system','content':'原权限'}, {'role':'user','content':'原问题'},
                 {'role':'assistant','content':'过时的自信断言','reasoning_content':'HIDDEN_REASONING'},
                 {'role':'assistant','content':'最新动作也声称全对','tool_calls':[
                     {'id':'edit','function':{'name':'file_edit','arguments':{'path':path}}}]},
                 {'role':'tool','tool_call_id':'edit','content':'真实编辑回执'}]
        view=await run.author_messages(history,'原问题',manager)
        self.assertEqual([m['role'] for m in view],['system','user'])
        payload=json.loads(view[1]['content'])
        self.assertEqual(payload['view_version'],'recipe-author-v2')
        self.assertEqual(payload['materials']['current_candidate']['candidate']['markdown'],edited['markdown'])
        self.assertIn('真实编辑回执',view[1]['content'])
        self.assertNotIn('过时的自信断言',json.dumps(view,ensure_ascii=False))
        self.assertNotIn('最新动作也声称全对',json.dumps(view,ensure_ascii=False))
        self.assertIn('过时的自信断言',json.dumps(saved,ensure_ascii=False))
        self.assertNotIn('HIDDEN_REASONING',json.dumps(saved))
        self.assertTrue(all(eid in saved for eid in payload['history_evidence_ids']))
        manager.archive=lambda row:None
        self.assertIs(await run.author_messages(history,'原问题',manager),history)
        self.assertNotIn(None,manager.evidence_ids)
        manager.archive=None
        self.assertIs(await run.author_messages(history,'原问题',manager),history)

    async def test_author_keeps_post_feedback_new_file_and_following_steps_across_checkpoint(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        run.review_records={'first':{'status':'COMPLETE','feedback_returned':True,
                                    'attempts':['reviews/1'],'checks':['核对首稿']}}
        saved={}
        def archive(row):
            key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        history=[{'role':'system','content':'原权限'},{'role':'user','content':'原问题'},
                 {'role':'assistant','content':'旧稿已经全对'},
                 {'role':'tool','tool_call_id':'review1','content':'REVIEW_READY'}]
        await run.author_messages(history,'原问题',manager)
        # Real failure: a script writes a new draft, then a listing replaces the
        # last tool batch. Both operations and the new path must remain visible.
        new_path=run.record['directory']+'/draft_v2.json'
        tail=[{'role':'assistant','content':'已另存修订稿，接着核对新稿','tool_calls':[
                  {'id':'write2','function':{'name':'code_exec','arguments':{'code':'write new draft'}}}]},
              {'role':'tool','tool_call_id':'write2','content':new_path},
              {'role':'assistant','content':'核实文件位置','tool_calls':[
                  {'id':'list','function':{'name':'file_list','arguments':{'path':run.record['directory']}}}]},
              {'role':'tool','tool_call_id':'list','content':'draft.json, draft_v2.json'}]
        history+=tail
        view=await run.author_messages(history,'原问题',manager)
        self.assertEqual(view[2:],tail)
        self.assertIn(new_path,json.dumps(view[2:],ensure_ascii=False))
        self.assertNotIn('旧稿已经全对',json.dumps(view,ensure_ascii=False))
        self.assertIn('最近提交核对',view[1]['content'])
        state=copy.deepcopy(run.checkpoint_state(archive))
        restored=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        restored.restore_state(state,saved.__getitem__)
        self.assertEqual((await restored.author_messages(history,'原问题',manager))[2:],tail)
        self.assertTrue(all('reasoning_content' not in row for row in saved.values()))

    async def test_author_new_feedback_resets_boundary_and_missing_anchor_preserves_original_view(self):
        _,run,_=await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        run.review_records={'first':{'status':'COMPLETE','feedback_returned':True,'attempts':['reviews/1']}}
        manager=SimpleNamespace(archive=fingerprint,evidence_ids=set())
        history=[{'role':'system','content':'权限'},{'role':'user','content':'问题'},
                 {'role':'tool','tool_call_id':'review1','content':'第一版核对'}]
        await run.author_messages(history,'问题',manager)
        projected=[{'role':'system','content':'权限'},{'role':'user','content':'问题'},
                   {'role':'tool','tool_call_id':'write2','content':'新工作稿'}]
        self.assertIs(await run.author_messages(projected,'问题',manager),projected)
        history+=projected[-1:]+[{'role':'tool','tool_call_id':'review2','content':'第二版核对'}]
        run.review_records['second']={'status':'COMPLETE','feedback_returned':True,'attempts':['reviews/2']}
        view=await run.author_messages(history,'问题',manager)
        self.assertEqual(len(view),2)
        self.assertEqual(json.loads(view[1]['content'])['latest_feedback']['attempts'],['reviews/2'])
        history+=[{'role':'assistant','content':'保留有依据的异议并发布'}]
        self.assertEqual((await run.author_messages(history,'问题',manager))[2:],history[-1:])

    async def test_real_loop_uses_recipe_packet_after_feedback_and_can_publish_with_disagreement(self):
        from plugins.finance.loop import FinanceLoop
        files,run,draft=await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        key=fingerprint(draft);row=run.review_records[key]
        row.update(feedback_returned=True,issues=[],checks=['已核对'],read_requests=[])
        run.record['current_candidate']={'sha256':key,'candidate':draft}
        saved={}
        def archive(value):
            eid=fingerprint(value);saved[eid]=copy.deepcopy(value);return eid
        async def process(messages):return messages
        seen=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**options):
                seen.append(messages)
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':'publish','function':{
                    'name':'brief_publish','arguments':{**draft,'review_ref':key+':'+row['evidence_sha256'],
                    'review_response':'原文支持当前结论，保留限制后发布'}}}]}
        agent=FinanceLoop(llm=Model(),router=SimpleNamespace(research=run),
            registry=SimpleNamespace(get_definitions=lambda **kw:[{'type':'function','function':{'name':'brief_publish'}}]),
            audit=SimpleNamespace(record=lambda *a:None),
            context_mgr=SimpleNamespace(process=process,archive=archive,reader=saved.__getitem__,evidence_ids=set()),
            prompt_builder=SimpleNamespace(build=lambda **kw:'原权限'))
        agent.quality_first_research=agent.turn_loop_enabled=True
        agent.continuing_checkpoint=True
        with patch('plugins.finance.research_material_view.ResearchMaterialView.apply',
                   side_effect=AssertionError('Recipe运行不能再走旧批次预览')):
            events=[e async for e in agent.run([{'role':'system','content':'原权限'},{'role':'user','content':'原问题'},
                {'role':'assistant','content':'过时的自信断言'}])]
        self.assertEqual(json.loads(seen[0][1]['content'])['view_version'],'recipe-author-v2')
        self.assertNotIn('过时的自信断言',json.dumps(seen[0],ensure_ascii=False))
        self.assertIsNotNone(run.published)
        self.assertEqual(events[-1].kind,'done')

    async def test_first_question_manifest_survives_later_research_creation(self):
        run=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        saved={}
        def archive(row):
            key=fingerprint(row);saved[key]=row;return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        first=run.capture_input([{'role':'user','content':'原始自然问题'}],{'call_id':'first'},manager)
        self.assertEqual(first['call_id'],'first')
        from plugins.finance.turn import FinanceTurn
        turn=FinanceTurn();turn.research=run
        state=copy.deepcopy(turn.checkpoint_state(archive))
        restored=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        turn.research=restored;turn.restore_state(state,saved.__getitem__)
        await restored.begin({'phase':'准备','scope':'原始自然问题','plan':['取证']})
        self.assertEqual(restored.record['request_materials'][0]['call_id'],'first')

    async def test_author_handoff_keeps_current_candidate_after_review(self):
        _,run,draft,agent,_=await review_tests.ReviewTests().setup_review([
            {'issues':[],'checks':['核对当前稿']}])
        run.record['research_contract_version']=2
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            _=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'id',[{'role':'user','content':'问题'}])]
        view=json.JSONDecoder().raw_decode(run.guidance().split('\n',1)[1])[0]
        self.assertEqual(view['current_candidate']['candidate']['markdown'],draft['markdown'])

    async def test_provider_captures_serialized_input_not_preprojection_messages(self):
        import httpx
        from core.providers import CompatibleClient, CallBudget
        from plugins.finance.loop import FinanceLoop
        run=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
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
        recipe=copy.deepcopy(run.record['recipe'])
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

    async def test_optional_stage_link_records_actual_unknown_not_completion(self):
        run=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        first=await run.begin({'phase':'准备','scope':'研究','plan':['阅读','计算']})
        stage=first['recipe']['stages'][1]['id']
        with self.assertRaisesRegex(ValueError,'active_stage_id'):
            await run.begin({'phase':'分析','active_stage_id':'invented'})
        await run.begin({'phase':'分析','active_stage_id':stage})
        await run.observe({'name':'code_exec','status':'error','result':{'status':'UNKNOWN'}})
        receipt=run.record['recipe']['execution'][-1]
        self.assertEqual(receipt['stage_id'],stage)
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertEqual(run.record['recipe']['stages'][1]['model_status'],'planned')

    async def test_missing_evidence_identifiers_have_specific_errors_before_dispatch(self):
        from plugins.finance.evidence_steps import evidence_steps
        for kind in ('finance','notices','profile','quote','notice'):
            with self.assertRaisesRegex(ValueError,'article' if kind=='notice' else 'symbol'):
                evidence_steps({'kind':kind})

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
