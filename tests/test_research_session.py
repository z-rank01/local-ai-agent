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

    async def test_unread_quote_and_chart_conflict_rejected(self):
        files, run, params = await self.prepare()
        changed = copy.deepcopy(params)
        changed['sources'][0]['quote'] = '收入没有风险'
        self.assertIn('error', await run.publish(changed))
        changed = copy.deepcopy(params)
        changed['charts'][0]['unit'] = '亿元'
        self.assertIn('error', await run.publish(changed))
        changed = copy.deepcopy(params)
        changed['charts'][0]['items'][0]['value'] = '10'
        self.assertIn('error', await run.publish(changed))
        changed = copy.deepcopy(params)
        changed['charts'][0]['items'][0]['value'] = '200'
        self.assertIn('error', await run.publish(changed))
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
        with self.assertRaises(ValueError):
            await run.publish({**params, 'draft_path': run.record['directory']+'/draft.json'})
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
        self.assertEqual(len(attempts), 3)
        self.assertEqual(events[-1].kind, 'error')
        self.assertIn('未完成交付', events[-1].text)
        self.assertEqual(run.record['status'], 'DELIVERY_INCOMPLETE')
        self.assertIsNone(run.published)

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
        for key, bad in [('source', 'calculation:1'), ('value', '0.1'), ('unit', '亿元')]:
            changed = copy.deepcopy(params)
            changed['calculations'][1]['inputs'][0][key] = bad
            with self.assertRaises(ValueError):
                validate_publication(changed, run.sources, run.read_text)

    async def test_chart_accepts_explicit_table_header_unit(self):
        _, run, params = await self.prepare()
        source = params['sources'][0]['id']
        quote = '本报告期比上年同期增减(%)\n营业收入\n13.84'
        run.read_text[source].append(quote)
        params['sources'][0]['quote'] = quote
        params['charts'][0].update(unit='%', items=[{'label': '营业收入增速', 'value': '13.84', 'source': source}])
        validate_publication(params, run.sources, run.read_text)
        params['charts'][0]['unit'] = '亿元'
        with self.assertRaises(ValueError):
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
