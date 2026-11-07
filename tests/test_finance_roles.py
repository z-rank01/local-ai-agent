import copy
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import config
from harness.contracts.subagents import Artifact, RoleResult
from harness.runtime.subagents.results import structured_result
from harness.runtime.subagents.spec import load_role_specs
from harness.contracts.subagents import RoleTask
from plugins.finance.research.roles import review_context_fingerprint
from plugins.finance.research.session import ResearchSession
from plugins.finance.research_review import review_messages
from tests import test_research_review as review_tests
from tests import test_research_session as session_tests
from tests.subagent_fixtures import role


class FinanceRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_recipe_accepts_only_read_author_excerpt(self):
        from plugins.finance.research.roles import adopt_author_materials
        _, run, _, batch_id = await self.prepare_roles()
        run.record['workspace_capabilities'] = True
        stages = run.record['recipe']['stages']
        stages[0]['required_roles'] = ['finance_evidence']
        roles = run.record['role_batches'][batch_id]['roles']
        for role_id in list(roles):
            if role_id != 'finance_evidence':
                del roles[role_id]
        source_id = next(iter(run.sources))
        material = {'sources':[{'id':source_id,'quote':'营业收入100元'}]}
        adopted = adopt_author_materials(run, material)
        self.assertEqual(adopted['sources'][0]['excerpt']['page'], 1)
        with self.assertRaisesRegex(ValueError, '实际读取材料'):
            adopt_author_materials(run, {'sources':[{'id':source_id,'quote':'营业收入300万元'}]})
        roles['finance_evidence']['status'] = 'FAILED'
        with self.assertRaisesRegex(ValueError, 'finance_evidence'):
            adopt_author_materials(run, material)

    async def prepare_roles(self):
        files, run, draft = await session_tests.ResearchSessionTests().prepare()
        run.record['research_contract_version'] = 3
        run.record['recipe']['schema_version'] = 3
        del run.check_review
        run.review_records.clear()
        source_id = next(iter(run.sources))
        await run.source_excerpts({'stdout':json.dumps({'source_excerpts':[{
            'source_id':source_id, 'sha256':run.sources[source_id]['sha256'],
            'page':1, 'start':0, 'end':8, 'quote':'营业收入100元'}]})})
        batch_id = 'finance-batch'
        async def add(role_id, kind, content):
            await run.record_role_result(batch_id, {**asdict(RoleResult(role_id=role_id,
                status='COMPLETE', artifacts=[Artifact(kind=kind, content=content,
                    producer_role=role_id, sha256='a' * 64)], issues=[])),
                'result_ref':'result:' + role_id})
        await add('finance_evidence', 'source_excerpts', {'excerpts':[
            {'id':'E1', 'source_id':source_id, 'quote':'营业收入100元', 'locator':'第1页',
             'page':1, 'start':0, 'end':8},
            {'id':'E2', 'source_id':source_id, 'quote':'营业收入',
             'locator':'模型声称第99页', 'page':99, 'start':999, 'end':1007},
            {'id':'unverified', 'source_id':source_id, 'quote':'原文没有这句话'}]})
        await add('finance_calculation', 'calculation', {
            'calculations':[{'id':'C1', 'operation':'multiply', 'unit':'元', 'evidence_refs':['E1'],
                'inputs':[{'source':source_id, 'value':'100', 'unit':'元', 'period':'2025'},
                          {'source':'constant:规模倍数', 'value':'2', 'unit':'倍', 'period':'不适用'}]},
                       {'id':'C2', 'operation':'sum', 'unit':'元', 'evidence_refs':['E1'],
                        'inputs':[{'source':source_id, 'value':'999', 'unit':'元', 'period':'2025'}]}],
            'figures':[{'id':'F1', 'title':'收入', 'unit':'元', 'period':'2025',
                        'items':[{'label':'收入', 'source':source_id, 'value':'100'}]},
                       {'id':'F2', 'title':'规模敏感性', 'unit':'元', 'period':'2025',
                        'items':[{'label':'规模', 'source':source_id, 'calculation':0}]}]})
        await add('finance_analysis', 'claims', {'claims':[
            {'text':'2025年营业收入100元', 'kind':'fact', 'entity':'示例公司', 'metric':'营业收入',
             'period':'2025', 'unit':'元', 'value':'100', 'evidence_refs':['E1']},
            {'text':'规模敏感性计算为200元，不是预测', 'kind':'calculation', 'period':'2025',
             'unit':'元', 'value':'200', 'evidence_refs':['C1'],
             'conditions':['仅用于演示计算', '不代表预测'], 'unresolved':['实际业务期间待核对']}]})
        await add('finance_review', 'claim_checks', {'claim_checks':[
            {'claim':'2025年营业收入100元', 'evidence_refs':['E1'], 'observed_scope':'原文收入',
             'unresolved':'后续期间未知', 'assessment':'conditional'}], 'read_requests':[]})
        return files, run, draft, batch_id

    async def test_manifest_declares_real_roles_without_finance_scheduler(self):
        from core.tool_registry import ToolRegistry
        roles = load_role_specs(Path('plugins'), ToolRegistry(config.TOOLS_DIR).declared_tools)
        expected = {'finance_evidence','finance_calculation','finance_analysis','finance_review'}
        self.assertTrue(expected <= set(roles))
        self.assertEqual(roles['finance_calculation'].optional_tools, frozenset({'code_exec'}))
        self.assertFalse(roles['finance_analysis'].tools)
        for role_id in expected:
            self.assertFalse(roles[role_id].tools & {'research_begin','brief_publish','agent_batch_create'})
        task = RoleTask(role=roles['finance_analysis'], text='分析')
        with self.assertRaisesRegex(ValueError, 'claims'):
            structured_result(task, '{"artifacts":[{"kind":"claims","content":{}}]}', status='COMPLETE')

    async def test_real_calculation_receipt_fails_shared_contract_without_repair(self):
        from core.tool_registry import ToolRegistry
        from harness.runtime.subagents.materials import role_contract, role_prompt
        roles = load_role_specs(Path('plugins'), ToolRegistry(config.TOOLS_DIR).declared_tools)
        spec = roles['finance_calculation']
        content = json.loads(Path('tests/fixtures/finance_calculation_contract.json').read_text(encoding='utf-8'))['content']
        original = copy.deepcopy(content)
        payload = {'artifacts':[{'kind':'calculation','content':content}]}
        with self.assertRaisesRegex(ValueError, '枚举'):
            structured_result(RoleTask(role=spec,text='计算'), json.dumps(payload), status='COMPLETE')
        self.assertEqual(content, original)
        schema = role_contract(spec)['output']['schema']
        self.assertIn(json.dumps(schema, ensure_ascii=False), role_prompt(spec))
        calculation = schema['properties']['calculations']['items']['properties']
        self.assertEqual(calculation['operation']['enum'], ['sum','subtract','multiply','divide'])
        self.assertIn('不能填摘录别名', calculation['inputs']['items']['properties']['source']['description'])
        self.assertIn(json.dumps({'artifacts':[{'kind':'calculation',
            'content':{'calculations':[], 'figures':[]}, 'evidence_refs':[]}], 'issues':[]},
            ensure_ascii=False), role_prompt(spec))
        incomplete = {'calculations':[{'id':'C1','operation':'divide','unit':'倍','evidence_refs':[],
            'inputs':[{'source':'calculation:0'}]}], 'figures':[]}
        with self.assertRaisesRegex(ValueError, r'calculations\[0\].inputs\[0\].*period'):
            structured_result(RoleTask(role=spec,text='计算'), json.dumps({'artifacts':[
                {'kind':'calculation','content':incomplete}]}), status='COMPLETE')

    async def test_real_unregistered_excerpts_report_errors_and_require_actual_read_receipt(self):
        from plugins.finance.research.material_view import role_material_view
        fixture = json.loads(Path('tests/fixtures/finance_excerpt_handoff.json').read_text(encoding='utf-8'))
        run = ResearchSession(session_tests.Files(), conversation_id='c', model='m', allowed=True)
        await run.begin({'scope':'真实摘录交接回放', 'plan':['读取'], 'phase':'准备'})
        source = await run.save_source({'url':fixture['url'], 'title':'年度报告片段',
            'sha256':'a'*64, 'fetched_at':'2026-11-03', 'page_count':219,
            'total_chars':234999, 'media_type':'application/pdf', 'content':fixture['initial_read'],
            '_source_file':{'base64':'cmF3', 'pages':fixture['pages']}})
        key = source['source_id']
        content = {'excerpts':[{**row, 'source_id':key} for row in fixture['excerpts']]}
        original = copy.deepcopy(content)
        result = {**asdict(RoleResult(role_id='finance_evidence', status='COMPLETE',
            artifacts=[Artifact(kind='source_excerpts', content=content,
                                producer_role='finance_evidence', sha256='b'*64)], issues=[])),
            'result_ref':'original-result'}
        # Actual failure: printing the role artifact is not a registered source read.
        await run.observe({'name':'code_exec', 'status':'ok', 'params':{},
                           'result':{'stdout':json.dumps({'artifacts':result['artifacts']})}})
        await run.record_role_result('unregistered', result)
        ref = 'role:unregistered:finance_evidence:0'
        failures = run.artifacts()[ref]['validation_errors']
        self.assertEqual([row['index'] for row in failures], [0, 1])
        self.assertEqual(failures[0]['id'], fixture['excerpts'][0]['id'])
        self.assertIn('source_excerpts', failures[0]['error'])
        self.assertEqual(run.record['role_aliases']['unregistered'], {})
        self.assertEqual(role_material_view(run, 'unregistered')['artifacts'][ref]['validation_errors'], failures)
        self.assertEqual(len(run.role_batch_summary('unregistered')['roles']['finance_evidence']['validation_errors']), 2)
        self.assertTrue(any('摘录' in question for question in run.record['recipe']['stages'][-1]['open_questions']))

        receipt = json.dumps({'source_excerpts':[{**row, 'sha256':'a'*64}
                                                for row in content['excerpts']]})
        split = len(receipt) // 2
        for chunk in (receipt[:split], receipt[split:]):
            await run.source_excerpts({'stdout':chunk})
        self.assertEqual(run.read_text[key], [fixture['initial_read']])
        await run.observe({'name':'code_exec', 'status':'ok', 'params':{}, 'result':{'stdout':receipt}})
        self.assertEqual(run.read_text[key], [fixture['initial_read']])
        await run.observe({'name':'code_exec','subagent':True,'status':'ok','params':{},'result':{'stdout':receipt}})
        await run.record_role_result('registered', result)
        aliases = run.record['role_aliases']['registered']
        self.assertIn(fixture['excerpts'][0]['id'], aliases)
        self.assertNotIn(fixture['excerpts'][1]['id'], aliases)
        mapped = run.artifacts()[aliases[fixture['excerpts'][0]['id']]]['excerpt']
        self.assertEqual(mapped['quote'], fixture['excerpts'][0]['quote'])
        self.assertEqual(mapped['page'], fixture['excerpts'][0]['page'])
        self.assertEqual(run.artifacts()['role:registered:finance_evidence:0']['validation_errors'][0]['index'], 1)
        self.assertEqual(run.artifacts()[ref]['validation_errors'], failures)
        self.assertEqual(content, original)
        self.assertEqual(result['artifacts'][0]['content'], original)

    async def test_role_artifacts_are_mapped_once_and_author_selects_without_rewriting(self):
        _, run, _, batch_id = await self.prepare_roles()
        original = copy.deepcopy(run.record['artifacts'])
        aliases = run.record['role_aliases'][batch_id]
        self.assertNotIn('unverified', aliases)
        self.assertEqual(run.role_materials(batch_id)['aliases']['C1'], aliases['C1'])
        self.assertEqual(run.role_batch_summary(batch_id)['aliases']['E1'], aliases['E1'])
        self.assertTrue(run.handoff_materials()['role_aliases'][batch_id])
        self.assertEqual(run.artifacts()[aliases['C1']]['calculation']['result'], '200')
        self.assertNotIn('C2', aliases)
        self.assertIn('F2', aliases)
        calculation = next(artifact for artifact in run.record['artifacts'].values()
                           if artifact.get('kind') == 'role_calculation')
        self.assertEqual(calculation['validation_errors'][0]['index'], 1)
        self.assertEqual(run.artifacts()[aliases['E2']]['excerpt']['locator'],
                         '原文已收到；页码与字符定位未由程序确认')
        handoff = run.handoff_materials()
        source = next(iter(handoff['sources'].values()))
        self.assertEqual(source['received_scope'], 'VERIFIED_ROLE_EXCERPTS')
        self.assertEqual(source['received'], ['营业收入', '营业收入100元'])
        self.assertTrue(any(row.get('kind') == 'role_claims' and 'content' not in row
                            for row in handoff['artifacts'].values()))
        self.assertEqual(handoff['artifacts'][aliases['C1']]['calculation']['result'], '200')
        role_stage = next(stage for stage in run.record['recipe']['stages']
                          if stage.get('producer_role') == 'finance_analysis')
        decision = role_stage['claims'][0]['id']
        normalized = await run.publication_input({'title':'期内收入已定位',
            'markdown':'2025年营业收入100元；后续期间未知。', 'as_of':'2025-12-31', 'stance':'等待验证',
            'sources':[{'artifact_ref':aliases['E1']}], 'calculation_refs':[aliases['C1']],
            'chart_refs':[aliases['F1']], 'decision_claim_ids':[decision]})
        self.assertEqual(normalized['sources'][0]['quote'], '营业收入100元')
        self.assertEqual(normalized['calculations'][0]['result'], '200')
        reviewed = json.loads(review_messages('原问题', normalized, run, '')[1]['content'])
        self.assertEqual(reviewed['evidence'],
                         {key:handoff[key] for key in ('sources', 'requests', 'reading')})
        source_id = normalized['sources'][0]['id']
        url = reviewed['publication_context']['source_links'][source_id]
        self.assertIn(f'[{source_id}]({url})', run.references(normalized))
        self.assertEqual(run.record['decision_claims'][0]['claim']['producer_role'], 'finance_analysis')
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            await run.begin({'phase':'分析', 'stage_updates':[{'id':role_stage['id'], 'assessment':'作者改写'}]})
        with self.assertRaisesRegex(ValueError, '分析角色'):
            await run.begin({'phase':'分析', 'stage_updates':[{'goal':'作者自填', 'claims':[{'text':'改写'}]}]})
        with self.assertRaisesRegex(ValueError, 'finance_evidence'):
            await run.begin({'phase':'获取', 'evidence_requests':[{'kind':'profile','symbol':'SH688178'}]})
        with self.assertRaisesRegex(ValueError, '已校验'):
            await run.publication_input({'sources':[{'artifact_ref':aliases['E1']}],
                'calculations':[{'operation':'sum','inputs':[],'unit':'元'}], 'decision_claim_ids':[decision]})
        self.assertEqual(run.record['artifacts'], original)
        first = review_context_fingerprint(run, normalized, '原问题')
        second = review_context_fingerprint(run, normalized, '另一个问题')
        self.assertNotEqual(first, second)
        self.assertEqual(first, review_context_fingerprint(run, normalized, '原问题'))
        run.method_text['method'] = '新方法正文'
        self.assertNotEqual(first, review_context_fingerprint(run, normalized, '原问题'))

    async def test_author_cannot_publish_before_all_role_results_or_use_unverified_claim(self):
        files = session_tests.Files()
        run = ResearchSession(files, conversation_id='c', model='m', allowed=True)
        await run.begin({'phase':'准备','scope':'公告', 'plan':['角色研究']})
        self.assertEqual(run.record['research_contract_version'], 3)
        self.assertEqual(run.record['recipe']['schema_version'], 3)
        with self.assertRaisesRegex(ValueError, '必要角色'):
            await run.publication_input({'decision_claim_ids':['invented']})
        self.assertIn('web_fetch', run.tool_names)
        self.assertTrue({'code_exec','shell_exec'} <= run.tool_names)
        files.known_tools = frozenset()
        self.assertEqual(run.tool_names, frozenset())
        del files.known_tools
        del run.record['author_workspace_scripts']
        del run.record['workspace_capabilities']
        self.assertFalse({'code_exec','shell_exec'} & run.tool_names)
        self.assertIn('agent_batch_create', run.tool_names)
        files, run, _, batch_id = await self.prepare_roles()
        aliases = run.record['role_aliases'][batch_id]
        stage = next(stage for stage in run.record['recipe']['stages']
                     if stage.get('producer_role') == 'finance_analysis')
        stage['claims'][0]['evidence_refs'] = ['missing']
        with self.assertRaisesRegex(ValueError, '分析角色'):
            await run.publication_input({'sources':[{'artifact_ref':aliases['E1']}],
                'decision_claim_ids':[stage['claims'][0]['id']]})

    async def test_failed_role_keeps_error_and_text_issues_in_recipe(self):
        _, run, _, _ = await self.prepare_roles()
        await run.record_role_result('failed-batch', asdict(RoleResult(role_id='finance_review',
            status='FAILED', error='upstream unavailable', issues=['文字形式的资料缺口'], artifacts=[])))
        stage = next(stage for stage in run.record['recipe']['stages']
                     if stage.get('batch_id') == 'failed-batch')
        self.assertEqual(stage['role_status'], 'FAILED')
        self.assertIn('文字形式的资料缺口', stage['open_questions'])
        self.assertIn('执行错误：upstream unavailable', stage['open_questions'])

    async def test_calculation_dependencies_keep_original_indices_after_partial_failure_and_selection(self):
        _, run, _, original_batch = await self.prepare_roles()
        source = next(iter(run.sources))
        batch = 'dependency-batch'
        run.record['role_aliases'][batch] = dict(run.record['role_aliases'][original_batch])
        base = {'operation':'sum', 'unit':'元', 'evidence_refs':['E1'],
                'inputs':[{'source':source, 'value':'100', 'unit':'元', 'period':'2025'}]}
        calculations = [dict(copy.deepcopy(base), id='D'+str(i)) for i in range(5)]
        calculations[0]['inputs'][0]['value'] = '999'
        calculations[1]['operation'] = 'multiply'
        calculations[1]['inputs'].append({'source':'constant:倍数', 'value':'2', 'unit':'倍', 'period':'不适用'})
        calculations[2]['inputs'] = [{'source':'calculation:1','period':'2025'}]
        calculations[3]['inputs'] = [{'source':'calculation:0','period':'2025'}]
        figures = [{'id':'G'+str(i),'title':'图','period':'2025','unit':'元',
                    'items':[{'label':'值','calculation':value}]} for i,value in enumerate((0,2))]
        content = {'calculations':calculations, 'figures':figures}
        original = copy.deepcopy(content)
        await run.record_role_result(batch, asdict(RoleResult(role_id='finance_calculation',
            status='COMPLETE', artifacts=[Artifact(kind='calculation', content=content,
                producer_role='finance_calculation', sha256='b'*64)])))
        aliases = run.record['role_aliases'][batch]
        for key in ('D0','D3','G0'):
            self.assertNotIn(key, aliases)
        self.assertTrue(aliases['D2'].endswith(':validated:2'))
        self.assertTrue(aliases['G1'].endswith(':figure:1'))
        self.assertEqual(run.artifacts()[aliases['D2']]['calculation']['result'], '200')
        artifact = next(a for a in run.artifacts().values() if a.get('sha256') == 'b'*64)
        self.assertEqual([e['index'] for e in artifact['validation_errors']], [0,3,'figures[0]'])
        self.assertEqual(artifact['content'], original)
        claim = next(s['claims'][0]['id'] for s in run.record['recipe']['stages']
                     if s.get('producer_role') == 'finance_analysis')
        params = await run.publication_input({'title':'依赖', 'markdown':'营业收入100元',
            'as_of':'2025','stance':'等待', 'sources':[{'artifact_ref':aliases['E1']}],
            'calculation_refs':[aliases['D4'],aliases['D2']], 'chart_refs':[aliases['G1']],
            'decision_claim_ids':[claim]})
        self.assertEqual([c['id'] for c in params['calculations']], ['D4','D1','D2'])
        self.assertEqual(params['calculations'][2]['inputs'][0]['source'], 'calculation:1')
        self.assertEqual(params['charts'][0]['items'][0]['calculation'], 2)
        self.assertEqual(content, original)

    async def test_v3_review_reuses_only_same_role_context_and_explicit_publish(self):
        files, run, _, batch_id = await self.prepare_roles()
        calls = []
        class Model:
            observes_native_requests = True
            async def chat_stream_with_tools(self, messages, tools=None, **options):
                calls.append(messages)
                options['request_observer']('native_model_result', {
                    'local_attempt_id':'v3-review', 'result_status':'COMPLETE'})
                yield '', {'content':json.dumps({'issues':[], 'checks':['核对当前角色材料']})}
        from plugins.finance.loop import FinanceLoop
        agent = FinanceLoop(llm=Model(), router=SimpleNamespace(research=run), registry=None,
            audit=SimpleNamespace(record=lambda *args:None), context_mgr=None, prompt_builder=None)
        aliases = run.record['role_aliases'][batch_id]
        claim = next(claim for stage in run.record['recipe']['stages']
                     if stage.get('producer_role') == 'finance_analysis'
                     for claim in stage['claims'] if claim['kind'] == 'fact')
        submission = {'title':'期内收入已定位', 'markdown':'2025年营业收入100元；后续期间未知。',
            'as_of':'2025-12-31', 'stance':'等待验证', 'sources':[{'artifact_ref':aliases['E1']}],
            'decision_claim_ids':[claim['id']]}
        history = [{'role':'user','content':'原公告问题'}]
        agent._turn_question = history[0]['content']
        path = run.record['directory'] + '/draft.json'
        files.files[path] = json.dumps(submission, ensure_ascii=False)
        with patch.object(config, 'PUBLISH_REVIEW_ENFORCE', False):
            events = [event async for event in agent._dispatch_tool(
                'brief_publish', {'draft_path':path}, 'c', None, 'one', history)]
            ready = next(event.data['result'] for event in events if event.kind == 'tool_result')
            self.assertEqual(ready['status'], 'REVIEW_READY')
            self.assertEqual(ready['same_draft_publish_parameters'],
                             {'draft_path':path, 'review_ref':ready['review_ref']})
            self.assertIsNone(run.published)
            record = run.review_records[ready['candidate_sha256']]
            self.assertEqual(record['same_draft_publish_parameters'],ready['same_draft_publish_parameters'])
            self.assertIn('file_edit',record['edit_instruction'])
            self.assertIn('code_exec',record['edit_instruction'])
            self.assertEqual(ready['source_links'],record['source_links'])
            self.assertIn('没有列出核心问题',record['publish_instruction'])
            self.assertEqual(record['review_context_sha256'], ready['review_ref'].split(':')[-1])
            context = json.loads(calls[0][1]['content'])
            self.assertEqual(context['question'], '原公告问题')
            self.assertTrue(context['artifacts'])
            stage_claims = {(stage['id'], item['id']): item for stage in context['recipe']['stages']
                            for item in stage.get('claims', [])}
            for row in context['claim_materials'] + context['decision_claims']:
                self.assertNotIn('claim', row)
                self.assertIn((row['stage_id'], row['claim_id']), stage_claims)
            changed = {**submission, 'markdown': submission['markdown'] + '新增陈述。',
                       'review_ref':ready['review_ref'], 'review_response':'发布'}
            events = [event async for event in agent._dispatch_tool(
                'brief_publish', changed, 'c', None, 'stale', history)]
            self.assertEqual(events[-1].data['result']['status'], 'REVIEW_REFERENCE_MISMATCH')
            self.assertEqual(len(calls), 1)
            self.assertIsNone(run.published)
            events = [event async for event in agent._dispatch_tool('brief_publish',
                {**ready['same_draft_publish_parameters'], 'review_response':'保留期内边界，明确发布'},
                'c', None, 'two', [*history, {'role':'user','content':'[系统对话摘要] 工作稿及历史核对摘要'}])]
            self.assertEqual(next(event.data['result']['status'] for event in events
                                  if event.kind == 'tool_result'), 'PUBLISHED')
        self.assertEqual(len(calls), 1)
        saved = json.loads(files.files[run.record['directory']+'/research.json'])
        self.assertEqual(saved['decision_claim_ids'], [claim['id']])
        self.assertEqual(saved['role_batches'][batch_id]['roles']['finance_evidence']['status'], 'COMPLETE')


if __name__ == '__main__':
    unittest.main()
