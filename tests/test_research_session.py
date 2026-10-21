import base64
import copy
import json
import unittest

from core.research_session import ResearchSession
from core.research_visuals import number, validate_publication


class Files:
    def __init__(self):
        self.files = {}
        self.fail = None

    async def dispatch(self, tool, params, session):
        if tool == 'shell_exec':
            assert params['command'].startswith('mkdir -p /workspace/reports/chat-research/')
            for child in ('sources', 'drafts', 'publication-attempts'):
                assert '/' + child in params['command']
            return {'exit_code': 0}
        if tool == 'file_read':
            content = self.files[params['path']]
            offset = params.get('offset', 0)
            end = offset + 100
            end = min(end, len(content))
            return {'content': content[offset:end]+'\n[读取提示：不是文件正文]',
                    'range': {'start': offset, 'end': end, 'total': len(content)},
                    'sha256': 'a'*64, 'next_offset': end if end < len(content) else None}
        assert tool == 'file_write'
        if params['path'].endswith(self.fail or '\0'):
            raise OSError('disk unavailable')
        self.files[params['path']] = params['content']
        return {'written': params['path']}


class ResearchSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_grouped_numbers_accept_only_valid_thousands_format(self):
        from decimal import Decimal, InvalidOperation
        self.assertEqual(number('1,123,645,275'), Decimal('1123645275'))
        self.assertEqual(number('-1,234.50'), Decimal('-1234.50'))
        with self.assertRaises(InvalidOperation):
            number('1,23')

    async def prepare(self):
        files = Files()
        run = ResearchSession(files, conversation_id='conversation', model='selected', allowed=True)
        await run.begin({'scope': '公司研究', 'plan': ['读原文'], 'phase': '准备'})
        await run.observe({'name': 'file_read', 'status': 'ok', 'params': {
            'path': '/workspace/skills/harness-stock-research-v1/references/company.md'},
            'result': {'content': '方法'}})
        source = await run.save_source({'url': 'https://example.org/report', 'title': '报告',
            'sha256': 'a'*64, 'fetched_at': '2026-10-18', 'page_count': 1,
            'total_chars': 8, 'media_type': 'text/html', 'content': '营业收入100元',
            '_source_file': {'base64': base64.b64encode(b'raw').decode(),
                             'pages': [{'page': 1, 'text': '营业收入100元'}]}})
        await run.begin({'phase': '核对'})
        params = {'title': '参考', 'markdown': '## 结论\n等待验证。', 'as_of': '2026-10-18',
            'stance': '等待验证', 'sources': [{'id': source['source_id'], 'locator': '第1页', 'quote': '营业收入100元'}],
            'calculations': [], 'charts': [{'title': '收入', 'unit': '元', 'period': '2025',
                'items': [{'label': '收入', 'value': '100', 'source': source['source_id']}]}],
            'review': {'checks': ['核对原值'], 'limitations': '未核对估值', 'conflicts': []}}
        # These cases isolate deterministic rendering. Review orchestration is
        # exercised separately with a real ResearchSession and a mock supplier.
        from unittest.mock import Mock
        from core.research_review import fingerprint
        run.check_review = Mock()
        run.review_records[fingerprint(params)]={'status':'COMPLETE','issues':[],
            'evidence_sha256':fingerprint(run.evidence_index())}
        return files, run, params

    async def test_publish_preserves_original_and_is_idempotent(self):
        files, run, params = await self.prepare()
        result = await run.publish(params)
        self.assertEqual(result, await run.publish(params))
        self.assertEqual(files.files[run.record['directory']+'/model-original.md'], params['markdown'])
        self.assertIn('<svg', files.files[result['path']])
        self.assertIn('selected', files.files[result['path']])
        self.assertFalse(run.active)
        with self.assertRaises(ValueError):
            await run.publish({**params, 'markdown': 'changed'})

    async def test_unauthorized_and_failed_publish_do_not_create_success(self):
        denied = ResearchSession(Files(), conversation_id='c', model='m', allowed=False)
        with self.assertRaises(PermissionError):
            await denied.begin({'scope': 'x', 'plan': ['x']})
        files, run, params = await self.prepare()
        files.fail = 'report.html'
        with self.assertRaises(OSError):
            await run.publish(params)
        self.assertIsNone(run.published)
        self.assertTrue(run.active)

    async def test_editorial_notes_are_saved_separately_without_rewriting_research(self):
        files, run, params = await self.prepare()
        for notes in ('订正记录', [1], ['']):
            self.assertIn('editorial_notes', (await run.publish({**params, 'editorial_notes': notes}))['error'])
        params['markdown'] += '\n原披露数值已更正；资料限制仍需说明。'
        params['editorial_notes'] = ['根据开发反馈调整段落与术语解释。']
        result = await run.publish(params)
        root = run.record['directory']
        self.assertEqual(files.files[root+'/model-original.md'], params['markdown'])
        self.assertIn(params['markdown'], files.files[root+'/report.md'])
        self.assertNotIn(params['editorial_notes'][0], files.files[result['path']])
        self.assertNotIn(params['editorial_notes'][0], files.files[root+'/report.md'])
        self.assertEqual(json.loads(files.files[root+'/editorial-notes.json'])['notes'], params['editorial_notes'])
        self.assertEqual(json.loads(files.files[root+'/evidence.json'])['editorial_notes'], params['editorial_notes'])
        self.assertEqual(result, await run.publish(params))

    async def test_directory_creation_failure_does_not_begin_research(self):
        class ReadOnly:
            async def dispatch(self, tool, params, session):
                self_tool = tool
                if self_tool != 'shell_exec':
                    raise AssertionError('must stop before writing files')
                return {'exit_code': 1, 'stderr': 'permission denied'}
        run = ResearchSession(ReadOnly(), conversation_id='c', model='m', allowed=True)
        with self.assertRaises(RuntimeError):
            await run.begin({'scope': '公司研究', 'plan': ['核对']})
        self.assertIsNone(run.record)

    async def test_actual_method_read_before_begin_is_retained_only_for_this_turn(self):
        files = Files()
        run = ResearchSession(files, conversation_id='c', model='m', allowed=True)
        path = '/workspace/skills/harness-stock-research-v3/references/finance.md'
        event = {'name':'file_read', 'status':'error', 'params':{'path':path},
                 'result':{'content':'方法', 'sha256':'b'*64}}
        await run.observe(event)
        self.assertFalse(run.methods)
        await run.observe({**event, 'status':'ok'})
        self.assertIn(path, run.methods)
        self.assertFalse(files.files)  # no research or file writes before begin
        reply = await run.begin({'scope':'公司', 'plan':['核对'], 'phase':'准备'})
        self.assertIn('本轮已成功读取的方法不必重读', reply['next'])
        saved = json.loads(files.files[reply['directory']+'/research.json'])
        self.assertIn(path, saved['methods'])
        fresh = ResearchSession(files, conversation_id='c', model='m', allowed=True)
        self.assertFalse(fresh.methods)
        self.assertFalse(run.read_text)  # method receipt is not source reading

    async def test_unread_quote_and_chart_conflict_rejected(self):
        files, run, params = await self.prepare()
        changed = copy.deepcopy(params)
        changed['sources'][0]['quote'] = '收入没有风险'
        self.assertIn('error', await run.publish(changed))
        changed = copy.deepcopy(params)
        changed['charts'][0]['items'][0]['value'] = '10'
        self.assertIn('error', await run.publish(changed))
        changed = copy.deepcopy(params)
        changed['charts'][0]['items'][0]['value'] = '200'
        self.assertIn('error', await run.publish(changed))
        self.assertIsNone(run.published)

    async def test_publication_errors_identify_missing_steps_and_unread_source(self):
        _, run, params = await self.prepare()
        run.record['phase'] = '分析'
        run.methods.clear()
        with self.assertRaisesRegex(ValueError, '尚未实际读取方法') as error:
            await run.publish(params)
        self.assertNotIn('尚无本轮公开来源', str(error.exception))
        run.record['phase'] = '核对'
        run.methods.add('/workspace/skills/harness-stock-research-v3/references/finance.md')
        params['sources'][0]['quote'] = '未实际读取的句子'
        result = await run.publish(params)
        self.assertIn('sources[0]', result['error'])
        self.assertIn(params['sources'][0]['id'], result['error'])
        self.assertIn('对应页', result['error'])
        self.assertIsNone(run.published)

    async def test_wrong_calculation_is_not_published(self):
        files, run, params = await self.prepare()
        params['calculations'] = [{'operation': 'divide', 'unit': '元', 'result': '51',
            'inputs': [{'value': '100', 'unit': '元', 'period': '2025', 'source': params['sources'][0]['id']},
                       {'value': '2', 'unit': '份', 'period': '2025', 'source': params['sources'][0]['id']}]}]
        self.assertIn('error', await run.publish(params))

    async def test_rejected_draft_can_be_repaired_and_published_by_path(self):
        files, run, params = await self.prepare()
        bad = copy.deepcopy(params)
        bad['charts'][0]['items'][0]['value'] = '200'
        result = await run.publish(bad)
        original = files.files[result['draft_path']]
        self.assertEqual(json.loads(original), bad)
        self.assertNotIn(run.record['directory']+'/report.html', files.files)
        path = run.record['directory']+'/working.json'
        files.files[path] = json.dumps(params, ensure_ascii=False)
        published = await run.publish({'draft_path': path})
        self.assertEqual(published, await run.publish({'draft_path': path}))
        self.assertEqual(files.files[result['draft_path']], original)
        self.assertEqual(json.loads(files.files[run.record['directory']+'/publication-attempts/2.json']), params)

    async def test_draft_cannot_cross_research_directory_or_mix_inputs(self):
        _, run, params = await self.prepare()
        for path in ['/workspace/other.json', run.record['directory']+'/../escape.json',
                     run.record['directory']+'-other/draft.json', run.record['directory']+'/..\\other.json']:
            with self.assertRaises(ValueError):
                await run.publish({'draft_path': path})
        # Inline fields may accompany draft_path again (inline wins); only a path
        # outside this research directory is rejected.
        await run.write('draft.json', params)
        merged = await run.publication_input({
            'draft_path': run.record['directory']+'/draft.json', 'title': '覆盖标题'})
        self.assertEqual(merged['title'], '覆盖标题')
        self.assertEqual(run.publication_attempts, 0)

    async def test_publication_uses_real_file_window_and_rejects_changing_snapshot(self):
        from tests.test_file_paging import FileOps
        from hashlib import sha256
        files, run, params = await self.prepare()
        params['markdown'] += '\n正文中的[已读到文件末尾]也必须保留。'
        path = run.record['directory']+'/working.json'
        text = json.dumps(params, ensure_ascii=False)
        class ActualWindow(Files):
            changing = False
            async def dispatch(self, tool, args, session):
                if tool == 'file_read':
                    offset = args.get('offset', 0)
                    digest = sha256(text.encode()).hexdigest()
                    if self.changing and offset:
                        digest = '0'*64
                    return FileOps._window(text, path, digest, offset, 80, {})
                return await super().dispatch(tool, args, session)
        actual = ActualWindow(); actual.files = files.files; run.router = actual
        actual.changing = True
        with self.assertRaisesRegex(ValueError, '发生变化'):
            await run.publish({'draft_path': path})
        actual.changing = False
        result = await run.publish({'draft_path': path})
        self.assertEqual(actual.files[run.record['directory']+'/model-original.md'], params['markdown'])
        self.assertEqual(result['status'], 'PUBLISHED')

    async def test_agent_continues_past_round_limit_and_expired_legacy_window(self):
        from core.agent import Agent
        from types import SimpleNamespace
        files, run, params = await self.prepare()
        calls = []
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                calls.append([t['function']['name'] for t in tools or []])
                if len(calls) <= 3:
                    name, args = 'code_exec', {'code': str(len(calls))}
                else:
                    name, args = 'brief_publish', params
                yield '', {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': str(len(calls)), 'function': {'name': name, 'arguments': args}}]}
        class Router:
            research = run
            async def dispatch(self, name, args, session):
                if name == 'brief_publish':
                    return await run.publish(args)
                return {'stdout': args['code']}
        async def process(messages):
            return messages
        agent = Agent(llm=Model(), router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw: [
            {'type': 'function', 'function': {'name': n}} for n in ('code_exec', 'brief_publish', 'stock_research_submit')]),
            audit=SimpleNamespace(record=lambda *a: None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw: 'test'), max_rounds=1)
        agent.stock_window_seconds = -1
        agent.quality_first_research = agent.stock_loop_enabled = True
        events = [e async for e in agent.run([{'role': 'user', 'content': '完成研究'}])]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all('brief_publish' in c for c in calls))
        self.assertTrue(all('stock_research_submit' not in c for c in calls))
        self.assertEqual(events[-1].kind, 'done')
        self.assertIn('阅读简报', events[-1].text)

    async def test_no_progress_closes_research_with_visible_error(self):
        from core.agent import Agent
        from types import SimpleNamespace
        _, run, _ = await self.prepare()
        attempts = []
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                attempts.append(1)
                yield '', {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': str(len(attempts)), 'function': {
                        'name': 'web_fetch', 'arguments': {'url': 'https://example.org/unavailable'}}}]}
        class Router:
            research = run
            async def dispatch(self, name, args, session):
                return {'error': 'source unavailable'}
        async def process(messages):
            return messages
        agent = Agent(llm=Model(), router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw: [
            {'type': 'function', 'function': {'name': 'web_fetch'}}]),
            audit=SimpleNamespace(record=lambda *a: None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw: 'test'), max_rounds=1)
        events = [e async for e in agent.run([{'role': 'user', 'content': '完成研究'}])]
        self.assertEqual(len([e for e in events if e.kind == 'tool_end']), 13)
        self.assertLessEqual(len(attempts), 26)  # bounded finalization; no cumulative research budget
        self.assertEqual(events[-1].kind, 'error')
        self.assertIn('未完成交付', events[-1].text)
        self.assertEqual(run.record['status'], 'DELIVERY_INCOMPLETE')
        self.assertIsNone(run.published)

    async def test_stalled_research_can_finalize_but_phase_changes_cannot_reopen_search(self):
        from core.agent import Agent
        from types import SimpleNamespace
        for publish in (True, False):
            with self.subTest(publish=publish):
                _, run, params = await self.prepare()
                await run.begin({'phase': '分析'})
                run.steps_without_progress = 12
                offered = []
                class Model:
                    cloud = False
                    async def chat_stream_with_tools(self, messages, tools):
                        names = {d['function']['name'] for d in tools or []}
                        offered.append(names)
                        if not names:
                            yield '不能发布。', {'role': 'assistant', 'content': '不能发布。'}
                            return
                        name, args = ('brief_publish', params) if publish and len(offered) == 2 else ('research_begin', {'phase': '核对'})
                        yield '', {'role': 'assistant', 'content': '', 'tool_calls': [
                            {'id': str(len(offered)), 'function': {'name': name, 'arguments': args}}]}
                class Router:
                    research = run
                    async def dispatch(self, name, args, session):
                        return await (run.publish(args) if name == 'brief_publish' else run.begin(args))
                async def process(messages):
                    return messages
                agent = Agent(llm=Model(), router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw: [
                    {'type': 'function', 'function': {'name': n}} for n in ('web_fetch', 'research_begin', 'brief_publish')]),
                    audit=SimpleNamespace(record=lambda *a: None), context_mgr=SimpleNamespace(process=process),
                    prompt_builder=SimpleNamespace(build=lambda **kw: 'test'), max_rounds=1)
                events = [e async for e in agent.run([{'role': 'user', 'content': '完成研究'}])]
                self.assertEqual(offered[:2], [{'research_begin', 'brief_publish'}]*2)
                if publish:
                    self.assertEqual(len(offered), 2)
                    self.assertEqual(events[-1].kind, 'done')
                    self.assertEqual(run.record['status'], 'PUBLISHED')
                else:
                    self.assertTrue(all('web_fetch' not in names for names in offered))
                    self.assertEqual(offered[-1], set())
                    self.assertLessEqual(len(offered), 15)
                    self.assertEqual(events[-1].kind, 'error')
                    self.assertIsNone(run.published)

    async def test_finalization_can_read_edit_review_publish_without_reopening_network(self):
        from types import SimpleNamespace
        from core.agent import Agent
        _,run,draft=await self.prepare()
        await run.begin({'phase':'分析'})
        run.steps_without_progress=12
        offered=[]
        steps=[('file_read',{'path':'/workspace/draft.json'}),('code_exec',{'code':'edit draft'}),
               ('research_begin',{'phase':'核对'}),('brief_publish',draft)]
        class Model:
            async def chat_stream_with_tools(self,messages,tools=None):
                offered.append({t['function']['name'] for t in tools})
                name,args=steps[len(offered)-1]
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':str(len(offered)),
                    'function':{'name':name,'arguments':args}}]}
        class Router:
            research=run
            async def dispatch(self,name,params,session):
                if name=='research_begin':return await run.begin(params)
                if name=='brief_publish':return await run.publish(params)
                return {'content':'existing draft'} if name=='file_read' else {'stdout':'edited','exit_code':0}
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':n}} for n in ('file_read','code_exec','research_begin','brief_publish','web_fetch')]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'system'),max_rounds=1)
        events=[e async for e in agent.run([{'role':'user','content':'完成研究'}])]
        self.assertEqual(events[-1].kind,'done')
        self.assertEqual(len(offered),4)
        self.assertTrue(all('web_fetch' not in names for names in offered))

    async def test_program_fills_derived_values_without_changing_submitted_draft(self):
        files, run, params = await self.prepare()
        source = params['sources'][0]['id']
        params['calculations'] = [
            {'operation': 'divide', 'unit': '万元', 'inputs': [
                {'value': '100', 'unit': '元', 'period': '2025', 'source': source},
                {'value': '10000', 'unit': '元/万元', 'period': '不适用', 'source': 'constant:单位换算'}]},
            {'operation': 'sum', 'unit': '万元', 'inputs': [
                {'unit': '万元', 'period': '2025', 'source': 'calculation:0'},
                {'unit': '万元', 'period': '2025', 'source': 'calculation:0'}]}]
        params['charts'][0].update(unit='万元', items=[{'label': '合计', 'calculation': 1}])
        submitted = copy.deepcopy(params)
        result = await run.publish(params)
        self.assertEqual(result['status'], 'PUBLISHED')
        self.assertEqual(params, submitted)
        self.assertEqual(json.loads(files.files[run.record['directory']+'/publication-attempts/1.json']), submitted)
        evidence = json.loads(files.files[run.record['directory']+'/evidence.json'])
        self.assertEqual(evidence['calculations'][0]['result'], '0.01')
        self.assertEqual(evidence['calculations'][1]['result'], '0.02')
        self.assertEqual(evidence['charts'][0]['items'][0]['value'], '0.02')
        self.assertEqual(result, await run.publish(submitted))

    async def test_program_result_keeps_decimal_precision_and_rejects_explicit_conflicts(self):
        _, run, params = await self.prepare()
        params['calculations'] = [{'operation': 'divide', 'unit': '元', 'inputs': [
            {'value': '100', 'unit': '元', 'period': '2025', 'source': params['sources'][0]['id']},
            {'value': '3', 'unit': '份', 'period': '不适用', 'source': 'constant:份数'}]}]
        checked = validate_publication(params, run.sources, run.read_text)
        self.assertEqual(checked['calculations'][0]['result'], '33.'+'3'*38)
        params['calculations'][0]['result'] = '33.33'
        self.assertIn('error', await run.publish(params))
        self.assertIsNone(run.published)

    async def test_quote_allows_layout_whitespace_but_not_omitted_words(self):
        _, run, params = await self.prepare()
        source = params['sources'][0]['id']
        run.read_text[source] = ['营业收入\u00a0100元。\nnot\nable']
        params['sources'][0]['quote'] = '营业收入  100元。 not able'
        validate_publication(params, run.sources, run.read_text)
        for quote in ('营业收入 100元。 notable', '营业收入 100元。 able'):
            params['sources'][0]['quote'] = quote
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                validate_publication(params, run.sources, run.read_text)

    async def test_calculation_dependencies_keep_values_and_units_bound(self):
        _, run, params = await self.prepare()
        source = params['sources'][0]['id']
        params['calculations'] = [
            {'operation': 'divide', 'inputs': [
                {'value': '100', 'unit': '元', 'period': '2025', 'source': source},
                {'value': '10000', 'unit': '元/万元', 'period': '不适用', 'source': 'constant:单位换算'}],
             'result': '0.01', 'unit': '万元'},
            {'operation': 'sum', 'inputs': [
                {'value': '0.01', 'unit': '万元', 'period': '2025', 'source': 'calculation:0'},
                {'value': '0.01', 'unit': '万元', 'period': '2025', 'source': 'calculation:0'}],
             'result': '0.02', 'unit': '万元'}]
        validate_publication(params, run.sources, run.read_text)
        for key, bad in [('source', 'calculation:1'), ('value', '0.1')]:
            changed = copy.deepcopy(params)
            changed['calculations'][1]['inputs'][0][key] = bad
            with self.assertRaises(ValueError):
                validate_publication(changed, run.sources, run.read_text)
        # A different unit must change the quantity's numeric representation.
        converted = copy.deepcopy(params)
        converted['calculations'][1]['inputs'][0]['unit'] = '亿元'
        with self.assertRaises(ValueError):
            validate_publication(converted, run.sources, run.read_text)
        del converted['calculations'][1]['inputs'][0]['value']
        checked = validate_publication(converted, run.sources, run.read_text)
        self.assertEqual(checked['calculations'][1]['inputs'][0]['value'], '0.000001')
        self.assertEqual(checked['calculations'][1]['result'], '0.02')

    async def test_mixed_money_units_and_chart_conversion_preserve_quantity(self):
        _, run, params = await self.prepare()
        source = params['sources'][0]['id']
        text = '营业收入100元，其他收入1万元'
        run.read_text[source] = [text]
        params['sources'][0]['quote'] = text
        params['calculations'] = [{'operation':'sum', 'unit':'元', 'inputs':[
            {'value':'100','unit':'元','period':'2025','source':source},
            {'value':'1','unit':'万元','period':'2025','source':source}]}]
        params['charts'][0].update(unit='万元', items=[{'label':'合计','calculation':0}])
        checked = validate_publication(params, run.sources, run.read_text)
        self.assertEqual(checked['calculations'][0]['result'], '10100')
        self.assertEqual(checked['charts'][0]['items'][0]['value'], '1.01')
        self.assertEqual(validate_publication(checked, run.sources, run.read_text), checked)
        params['charts'][0]['unit'] = '%'
        with self.assertRaisesRegex(ValueError, '不能换算单位'):
            validate_publication(params, run.sources, run.read_text)

    async def test_optional_publication_metadata_does_not_crash_renderer(self):
        files, run, params = await self.prepare()
        del params['review']
        del params['sources'][0]['locator']
        params['charts'] = []
        result = await run.publish(params)
        html = files.files[result['path']]
        self.assertIn('未记录具体位置', html)
        self.assertIn('未单独说明资料限制', html)

    async def test_legacy_complete_json_requires_explicit_size_and_no_next_page(self):
        files, run, params = await self.prepare()
        text = json.dumps(params)
        from unittest.mock import AsyncMock
        run.router = type('Reader', (), {'dispatch': AsyncMock(return_value={
            'content':text,'total_chars':len(text),'next_offset':None})})()
        self.assertEqual(await run.read_json('unused'), params)
        for result in ({'content':text}, {'content':text,'total_chars':len(text)+1},
                       {'content':text,'total_chars':len(text),'next_offset':len(text)},
                       {'content':text,'range':{'start':0,'end':len(text),'total':len(text)+1},'next_offset':None}):
            run.router.dispatch.return_value = result
            with self.assertRaises(ValueError):
                await run.read_json('unused')

    async def test_chart_accepts_explicit_table_header_unit(self):
        _, run, params = await self.prepare()
        source = params['sources'][0]['id']
        quote = '本报告期比上年同期增减(%)\n营业收入\n13.84'
        run.read_text[source].append(quote)
        params['sources'][0]['quote'] = quote
        params['charts'][0].update(unit='%', items=[{'label': '营业收入增速', 'value': '13.84', 'source': source}])
        validate_publication(params, run.sources, run.read_text)
        # The chart unit is no longer required to appear literally in the quote;
        # the plotted value is still located in the source text.
        params['charts'][0]['unit'] = '亿元'
        validate_publication(params, run.sources, run.read_text)

    async def test_stop_preserves_material_and_records_incomplete(self):
        import asyncio
        from core.agent import Agent
        from types import SimpleNamespace
        files, run, _ = await self.prepare()
        saved_sources = copy.deepcopy(run.sources)
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                raise asyncio.CancelledError()
                yield  # async generator, interrupted before any supplier response
        async def process(messages):
            return messages
        agent = Agent(llm=Model(), router=SimpleNamespace(research=run),
            registry=SimpleNamespace(get_definitions=lambda **kw: []),
            audit=SimpleNamespace(record=lambda *a: None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw: 'test'))
        with self.assertRaises(asyncio.CancelledError):
            _ = [e async for e in agent.run([{'role': 'user', 'content': '停止'}])]
        self.assertEqual(run.record['status'], 'DELIVERY_INCOMPLETE')
        self.assertEqual(run.sources, saved_sources)
        self.assertIn('DELIVERY_INCOMPLETE', files.files[run.record['directory']+'/research.json'])
