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
    async def test_manifest_ranges_are_archived_once_and_failure_does_not_register_reference(self):
        _,run,_=await session_tests.ResearchSessionTests().prepare()
        saved={}
        def archive(row):
            key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        run.method_text={f'method-{i}':'真实材料'+str(i) for i in range(300)}
        history=[{'role':'user','content':'\n'.join(run.method_text.values())}]
        for i in range(47):
            index=run.capture_input(history,{'call_id':str(i)},manager)
            original=json.loads(saved[index['manifest_ref']]['content'])
            methods=[row for row in original['materials'] if row['kind']=='method']
            self.assertEqual(len(methods),300)
            self.assertTrue(all(row['included'] for row in methods))
            self.assertIn(index['manifest_ref'],manager.evidence_ids)
        # Regression for the 47 manifests that pushed research.json over 10 MiB.
        self.assertLess(len(json.dumps(run.request_materials).encode()),100000)
        self.assertGreater(sum(len(row['content'].encode()) for row in saved.values()
            if row.get('record_kind')=='research_request_manifest'),1000000)
        before=copy.deepcopy(run.request_materials)
        def failing(row):
            if row.get('record_kind')=='research_request_manifest':
                raise OSError('archive unavailable')
            return archive(row)
        manager.archive=failing
        with self.assertRaisesRegex(OSError,'archive unavailable'):
            run.capture_input(history,{'call_id':'failed'},manager)
        self.assertEqual(run.request_materials,before)

    async def test_shared_artifacts_keep_claim_evidence_without_repeated_bodies(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        await run.begin({'phase':'分析','stage_updates':[{'goal':'解释原文'}]})
        ref='source:shared'
        run.record['artifacts'][ref]={'kind':'source','path':'/workspace/source.txt','sha256':'a'*64}
        stage=run.record['recipe']['stages'][-1]['id']
        await run.publication_input({**draft,'stage_updates':[{'id':stage,'claims':[
            {'text':'条件结论'+str(i),'kind':'inference','conditions':'同期间',
             'counterevidence':'相反信号','unresolved':'缺附注','evidence_refs':[ref]}
            for i in range(4)]}]})
        # Historical/missing navigation stays explicit; new submissions reject it.
        for claim in run.record['recipe']['stages'][-1]['claims']:
            claim['evidence_refs'].append('missing')
        author=run.handoff_materials()
        reviewer=json.loads(review_messages('问题',draft,run,'')[1]['content'])
        for view in (author,reviewer):
            self.assertEqual(view['artifacts'][ref]['path'],'/workspace/source.txt')
            for row in view['claim_materials'][-4:]:
                self.assertEqual(row['claim']['counterevidence'],'相反信号')
                self.assertNotIn('artifact',row['materials'][0])
                self.assertFalse(row['materials'][0]['missing'])
                self.assertTrue(row['materials'][1]['missing'])

    async def test_submission_stage_results_reach_writer_and_reviewer_without_changing_draft(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        await run.begin({'phase':'分析','stage_updates':[{'goal':'解释原文'}]})
        stage=run.record['recipe']['stages'][-1]['id']
        await run.observe({'name':'code_exec','status':'ok','result':{'stdout':'actual'}})
        ref=list(run.record['artifacts'])[-1]
        patch_value={'id':stage,'assessment':'有条件成立','model_status':'partial','output_refs':[ref],
                     'claims':[{'text':'有条件结论','kind':'inference','conditions':'同期间',
                                'unresolved':'缺附注','evidence_refs':[ref]}]}
        normalized=await run.publication_input({**draft,'stage_updates':[patch_value]})
        self.assertNotIn('stage_updates',normalized)
        claim=run.record['recipe']['stages'][-1]['claims'][0]
        self.assertTrue(claim['id'].startswith('claim_'))
        handoff=run.handoff_materials()['claim_materials']
        payload=json.loads(review_messages('原问题',normalized,run,'')[1]['content'])
        self.assertEqual(payload['claim_materials'],handoff)
        self.assertEqual(payload['publication_context']['phase'],'prepublication')
        self.assertEqual(handoff[-1]['claim']['unresolved'],'缺附注')
        prior=copy.deepcopy(run.record['recipe'])
        await run.publication_input({**draft,'stage_updates':[{'id':stage,'claims':[{**claim,'text':'收窄结论'}]}]})
        self.assertEqual(run.record['recipe']['stages'][-1]['claims'][0]['id'],claim['id'])
        self.assertEqual(run.record['recipe_history'][-1],prior)

    async def test_repeated_saved_submission_does_not_create_duplicate_stage(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        submission={**draft,'stage_updates':[{'goal':'核对现金流','assessment':'仍缺附注'}]}
        await run.publication_input(submission)
        recipe=copy.deepcopy(run.record['recipe'])
        await run.publication_input(submission)
        self.assertEqual(run.record['recipe'],recipe)

    async def test_each_external_submission_prepares_candidate_once(self):
        _,run,draft,agent,_=await review_tests.ReviewTests().setup_review([
            {'issues':[],'checks':['核对当前稿']}])
        run.record['research_contract_version']=2
        history=[{'role':'user','content':'原问题'}]
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False), \
             patch.object(run,'publication_input',wraps=run.publication_input) as resolve, \
             patch.object(run.publication,'validate_publication',
                          wraps=run.publication.validate_publication) as validate, \
             patch.object(run,'register_candidate_artifacts',
                          wraps=run.register_candidate_artifacts) as register:
            events=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'first',history)]
            ready=next(e.data['result'] for e in events if e.kind=='tool_result')
            events=[e async for e in agent._dispatch_tool('brief_publish',{**draft,
                'review_ref':ready['review_ref'],'review_response':'核对后明确发布'},
                'c',None,'second',history)]
        self.assertEqual(next(e.data['result']['status'] for e in events
                              if e.kind=='tool_result'),'PUBLISHED')
        self.assertEqual(resolve.call_count,2)
        self.assertEqual(validate.call_count,2)
        self.assertEqual(register.call_count,2)

    async def test_old_outcome_is_marked_for_both_roles_without_rewriting_history(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        await run.begin({'phase':'分析','stage_updates':[{'goal':'判断'}]})
        old=await run.publication_input(draft)
        run.register_candidate_artifacts(old)
        ref='candidate:'+fingerprint(old)
        stage=run.record['recipe']['stages'][-1]['id']
        await run.begin({'phase':'分析','stage_updates':[{'id':stage,'assessment':'旧判断','output_refs':[ref],
            'claims':[{'text':'旧判断','evidence_refs':[ref]}]}]})
        before=copy.deepcopy(run.record['recipe'])
        revised={**old,'markdown':'新的有边界结论'}
        run.record['current_candidate']={'candidate':revised,'sha256':fingerprint(revised)}
        author=run.handoff_materials()
        reviewer=json.loads(review_messages('问题',revised,run,'')[1]['content'])
        for view in (author,reviewer):
            self.assertEqual(view['recipe']['stages'][-1]['outcome_version']['status'],'OLDER_OR_UNVERIFIED_CANDIDATE')
        self.assertTrue(author['recipe_advice'])
        self.assertEqual(run.record['recipe'],before)
        run.register_candidate_artifacts(revised)
        await run.begin({'phase':'分析','stage_updates':[{'id':stage,'assessment':'收窄判断',
            'output_refs':['candidate:'+fingerprint(revised)]}]})
        self.assertEqual(run.handoff_materials()['recipe']['stages'][-1]['outcome_version']['status'],'CURRENT_TEXT_REFERENCE')

    async def test_handoff_summarizes_unused_calculations_and_excludes_execution_noise(self):
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        calculation={'operation':'divide','unit':'倍','result':'2','inputs':[
            {'source':'constant:示例','period':'不适用','unit':'元','value':'2'},
            {'source':'constant:示例','period':'不适用','unit':'元','value':'1'}]}
        used={**draft,'calculations':[calculation]}
        unused={**draft,'markdown':'另一稿','calculations':[calculation]}
        run.register_candidate_artifacts(used,validated=True)
        run.register_candidate_artifacts(unused,validated=True)
        used_ref='calculation:'+fingerprint(used)+':0'
        unused_ref='calculation:'+fingerprint(unused)+':0'
        stage=run.record['recipe']['stages'][0]['id']
        await run.begin({'phase':'分析','stage_updates':[{'id':stage,'claims':[
            {'text':'计算结论','kind':'calculation','evidence_refs':[used_ref]}]}]})
        artifacts=run.artifacts()
        self.assertIn('inputs',artifacts[used_ref])
        self.assertNotIn('inputs',artifacts[unused_ref])
        self.assertIn('inputs',run.record['artifacts'][unused_ref])
        recipe=run.handoff_materials()['recipe']
        self.assertNotIn('execution',recipe)
        self.assertNotIn('active_stage_id',recipe)

    async def test_author_feedback_batch_does_not_duplicate_large_projections(self):
        _,run,draft,path,manager,history,saved=await self.prepare_refresh()
        history += [
            {'role':'assistant','content':'','tool_calls':[{'id':'publish','function':{
                'name':'brief_publish','arguments':{'draft_path':path}}}]},
            {'role':'tool','tool_call_id':'publish','tool_name':'brief_publish',
             'content':json.dumps({'status':'REVIEW_READY','review_ref':'returned-ref',
                'review':{'issues':['完整意见只保留在latest_feedback']},
                'recipe':{'stages':['不重复交接']},'artifacts':{'candidate':'不重复交接'}})}]
        with patch('plugins.finance.research.revision.read_document',
                   return_value=(json.dumps(draft),'file-hash')):
            view=await run.author_messages(history,'原问题',manager)
        payload=json.loads(view[1]['content'])
        tool=payload['recent_tool_batch'][-1]
        compact=json.loads(tool['content'])
        self.assertEqual(compact['status'],'REVIEW_READY')
        self.assertEqual(compact['review_ref'],'returned-ref')
        self.assertNotIn('review',compact)
        self.assertNotIn('recipe',compact)
        self.assertNotIn('artifacts',compact)
        archived=next(row for row in saved.values() if row.get('tool_name')=='brief_publish')
        self.assertEqual(json.loads(archived['content'])['recipe']['stages'],['不重复交接'])
        self.assertEqual(payload['latest_feedback']['review_ref'],'returned-ref')

    async def prepare_refresh(self):
        files,run,draft=await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version']=2
        path=run.record['directory']+'/draft.json'
        run.record['working_draft_path']=path
        run.record['current_candidate']={'path':path,'candidate':copy.deepcopy(draft),
            'sha256':fingerprint(draft),'validation_status':'WORKING_NOT_CERTIFIED'}
        run.review_records={'review':{'status':'COMPLETE','feedback_returned':True,
            'attempts':['reviews/1'],'candidate_sha256':fingerprint(draft),
            'evidence_sha256':'evidence','native_call_id':'actual-call','review_ref':'returned-ref'}}
        saved={}
        def archive(row):
            key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
        manager=SimpleNamespace(archive=archive,evidence_ids=set())
        history=[{'role':'system','content':'permissions'},{'role':'user','content':'原问题'},
                 {'role':'tool','tool_call_id':'review','content':'实际反馈'}]
        return files,run,draft,path,manager,history,saved

    async def test_author_refresh_timeout_is_visible_not_fresh_and_survives_checkpoint(self):
        import httpx
        files,run,draft,path,manager,history,saved=await self.prepare_refresh()
        with patch('plugins.finance.research.revision.read_document',side_effect=httpx.ConnectTimeout('SECRET')) as read:
            view=await run.author_messages(history,'原问题',manager)
        self.assertEqual(read.call_count,1)
        payload=json.loads(view[1]['content']);current=payload['materials']['current_candidate']
        self.assertEqual(current['validation_status'],'READ_FAILED')
        self.assertEqual(current['previous_snapshot']['candidate'],draft)
        self.assertEqual(payload['latest_feedback']['review_ref'],'returned-ref')
        self.assertNotIn('SECRET',view[1]['content'])
        receipt=run.artifacts()[current['read_receipt_ref']]
        self.assertEqual(receipt['tool'],'author_candidate_refresh')
        self.assertEqual(receipt['result_status'],'FAILED')
        manifest=run.capture_input(view,{'local_attempt_id':'next-call'},manager)
        manifest=json.loads(saved[manifest['manifest_ref']]['content'])
        missing=next(x for x in manifest['materials'] if x['kind']=='current_candidate')
        self.assertFalse(missing['included']);self.assertEqual(missing['coverage'],'READ_FAILED')
        self.assertIsNone(missing['sha256'])
        self.assertTrue(next(x for x in manifest['materials'] if x['kind']=='previous_candidate')['included'])
        state=copy.deepcopy(run.checkpoint_state(manager.archive))
        restored=ResearchSession(files,conversation_id='c',model='frozen',allowed=True)
        restored.restore_state(state,saved.__getitem__)
        with patch('plugins.finance.research.revision.read_document',side_effect=httpx.ConnectTimeout('SECRET')):
            await restored.author_messages(history,'原问题',manager)
        old=restored.record['current_candidate']['previous_snapshot']
        self.assertEqual(old['candidate'],draft);self.assertNotIn('previous_snapshot',old)
        changed={**draft,'markdown':'实际回读的新稿'}
        with patch('plugins.finance.research.revision.read_document',return_value=(json.dumps(changed),'file-hash')):
            recovered=await restored.author_messages(history,'原问题',manager)
        current=json.loads(recovered[1]['content'])['materials']['current_candidate']
        self.assertEqual(current['candidate']['markdown'],'实际回读的新稿')
        self.assertEqual(current['validation_status'],'WORKING_NOT_CERTIFIED')
        self.assertEqual(sum(x['tool']=='author_candidate_refresh' for x in restored.trace),2)
        self.assertIsNone(restored.published)

    async def test_author_refresh_permission_revocation_is_not_swallowed(self):
        _,run,_,_,manager,history,_=await self.prepare_refresh()
        with patch('plugins.finance.research.revision.read_document',side_effect=PermissionError('revoked')):
            with self.assertRaises(PermissionError):await run.author_messages(history,'原问题',manager)
        self.assertFalse(any(x['tool']=='author_candidate_refresh' for x in run.trace))

    async def test_author_refresh_failure_cannot_hide_checkpoint_save_failure(self):
        import httpx
        from unittest.mock import AsyncMock
        _,run,_,_,manager,history,_=await self.prepare_refresh()
        with patch('plugins.finance.research.revision.read_document',side_effect=httpx.ConnectTimeout('connect timeout')), \
             patch.object(run,'checkpoint',new=AsyncMock(side_effect=RuntimeError('save failed'))):
            with self.assertRaisesRegex(RuntimeError,'save failed'):
                await run.author_messages(history,'原问题',manager)
        self.assertEqual(run.record['current_candidate']['validation_status'],'READ_FAILED')
        self.assertIsNone(run.published)

    async def test_recipe_v2_can_register_evidence_without_reading_methods(self):
        run=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
        await run.begin({'phase':'准备','scope':'公开资料','plan':['取证']})
        run.record['research_contract_version']=2
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
        run.record['research_contract_version'] = 2
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
        self.assertIsNone(receipt['stage_id'])
        self.assertEqual(receipt['selected_stage_id'],stage)
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
        self.assertNotIn('materials',manifest)
        self.assertEqual(run.request_materials[-1],manifest)
        manifest=json.loads(archive[manifest['manifest_ref']]['content'])
        self.assertTrue(all(row['included'] for row in manifest['materials']))
        self.assertNotIn('HIDDEN',json.dumps(archive))
        self.assertEqual(set(archive),manager.evidence_ids)
        absent=run.capture_input([{'role':'user','content':'没有原文'}],{'call_id':'next'},manager)
        absent=json.loads(archive[absent['manifest_ref']]['content'])
        self.assertTrue(all(not row['included'] for row in absent['materials']))
        draft['review']['conflicts']=['指标可能错配']
        draft['editorial_notes']=['自称已经修复全部核心错误']
        review=json.loads(review_messages('原问题',draft,run,'permissions')[1]['content'])
        self.assertEqual(review['known_conflicts'],['指标可能错配'])
        self.assertIn('recipe',review)
        self.assertNotIn('review',review['candidate'])
        self.assertNotIn('editorial_notes',review['candidate'])
        self.assertNotIn('自称已经修复全部核心错误',json.dumps(review,ensure_ascii=False))
        self.assertEqual(review['candidate']['markdown'],draft['markdown'])
        self.assertEqual(review['decision_surface']['title'],draft['title'])
        self.assertIn('等待验证',review['decision_surface']['opening'])
        self.assertEqual(draft['editorial_notes'],['自称已经修复全部核心错误'])

    async def test_claim_reference_observations_return_to_author_without_certification_or_veto(self):
        from plugins.finance.research_review import parse_review
        outputs=[]
        files,run,draft,agent,calls=await review_tests.ReviewTests().setup_review(outputs)
        run.record['research_contract_version']=2
        sid=draft['sources'][0]['id']
        outputs.append({'issues':[],'checks':['只核查可定位材料'], 'claim_checks':[
            {'claim':'等待验证。','evidence_refs':[{'source_id':sid,'quote':'营业收入100元','observed_at':'原文期内'}],
             'observed_scope':'只观察到期内收入','unresolved':'尚未证明后续变化','assessment':'conditional'},
            {'claim':'原稿没有这句话','semantic_certification':True,
             'evidence_refs':[{'source_id':'Sunknown','quote':'伪造引句','quote_received':True}],
             'observed_scope':'作者意见','unresolved':'引用不成立','assessment':'supported'}]})
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            events=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'id',[{'role':'user','content':'问题'}])]
            ready=next(e.data['result'] for e in events if e.kind=='tool_result')
            observed=ready['review']['claim_provenance']
            self.assertTrue(observed[0]['literal_claim_found'])
            self.assertTrue(observed[0]['references'][0]['quote_received'])
            self.assertFalse(observed[1]['literal_claim_found'])
            self.assertFalse(observed[1]['references'][0]['known_source'])
            self.assertFalse(observed[1]['references'][0]['quote_received'])
            self.assertNotIn('semantic_certification',ready['review']['claim_checks'][1])
            self.assertNotIn('quote_received',ready['review']['claim_checks'][1]['evidence_refs'][0])
            self.assertTrue(all(not row['semantic_certification'] for row in observed))
            events=[e async for e in agent._dispatch_tool('brief_publish',{**draft,
                'review_ref':ready['review_ref'],'review_response':'第二条意见依据不成立；按已取得原文保留限制后发布'},
                'c',None,'id',[{'role':'user','content':'问题'}])]
        self.assertEqual(next(e.data['result']['status'] for e in events if e.kind=='tool_result'),'PUBLISHED')
        self.assertEqual(len(calls),1)
        evidence=json.loads(files.files[run.record['directory']+'/evidence.json'])
        self.assertEqual(evidence['separate_review']['claim_provenance'],observed)
        self.assertNotIn('claim_checks',parse_review(json.dumps({'issues':[],'checks':['旧契约核对']})))

    async def test_claim_references_do_not_accept_unreceived_saved_text_or_paraphrased_quotes(self):
        from plugins.finance.research_review import claim_provenance,parse_review
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        sid=draft['sources'][0]['id']
        value=parse_review(json.dumps({'issues':[],'checks':['引用定位'], 'claim_checks':[
            {'claim':'等待验证。','assessment':'unsupported','evidence_refs':[
                {'source_id':sid,'quote':'营业收入增加至100元'},
                {'source_id':sid,'quote':'已保存但未返回的内容'}]}]},ensure_ascii=False))
        result=claim_provenance(value['claim_checks'],draft,run)
        self.assertTrue(all(row['known_source'] and not row['quote_received'] for row in result[0]['references']))
        with self.assertRaises(ValueError):
            parse_review(json.dumps({'issues':[],'checks':['格式'], 'claim_checks':[{'claim':'原句','evidence_refs':['路径']}]}))

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

    async def test_returned_publish_reference_survives_other_steps_and_checkpoint(self):
        _,run,draft,agent,calls=await review_tests.ReviewTests().setup_review([
            {'issues':[],'checks':['已实际核对当前稿']}])
        run.record['research_contract_version']=2
        history=[{'role':'system','content':'原权限'},{'role':'user','content':'原问题'}]
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            events=[e async for e in agent._dispatch_tool('brief_publish',draft,'c',None,'review1',history)]
            ready=next(e.data['result'] for e in events if e.kind=='tool_result')
            history += [{'role':'assistant','content':'','tool_calls':[{'id':'review1','function':{
                'name':'brief_publish','arguments':draft}}]},
                {'role':'tool','tool_call_id':'review1','content':json.dumps(ready,ensure_ascii=False)}]
            saved={}
            def archive(row):
                key=fingerprint(row);saved[key]=copy.deepcopy(row);return key
            manager=SimpleNamespace(archive=archive,evidence_ids=set())
            await run.author_messages(history,'原问题',manager)
            for call_id in ('read','calculate'):
                history += [{'role':'assistant','content':'继续核查','tool_calls':[{'id':call_id,'function':{
                    'name':'code_exec','arguments':{'code':'print(1)'}}}]},
                    {'role':'tool','tool_call_id':call_id,'content':'实际工具回执'}]
                view=await run.author_messages(history,'原问题',manager)
                self.assertEqual(json.loads(view[1]['content'])['latest_feedback']['review_ref'],ready['review_ref'])
            state=copy.deepcopy(run.checkpoint_state(archive))
            restored=ResearchSession(session_tests.Files(),conversation_id='c',model='frozen',allowed=True)
            restored.restore_state(state,saved.__getitem__)
            restored_view=await restored.author_messages(history,'原问题',manager)
            ref=json.loads(restored_view[1]['content'])['latest_feedback']['review_ref']
            self.assertEqual(ref,ready['review_ref'])
            events=[e async for e in agent._dispatch_tool('brief_publish',{**draft,'review_ref':ref,
                'review_response':'核查后采用当前稿并保留限制，明确发布'},'c',None,'publish',history)]
        self.assertEqual(next(e.data['result']['status'] for e in events if e.kind=='tool_result'),'PUBLISHED')
        self.assertEqual(len(calls),1)

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
