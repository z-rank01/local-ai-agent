import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from core.research_session import ResearchSession
from tests.test_research_session import Files

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


links = load('json_links', 'skills/websearch/source_document.py')
urls = load('source_urls', 'skills/runner/packages/harness-public-sources-v1/scripts/source_urls.py')
reader = load('read_json', 'skills/runner/packages/harness-public-sources-v1/scripts/read_json.py')


class PublicSourceHelpersTests(unittest.TestCase):
    def test_real_links_only_no_script_execution_or_synthesized_pdf(self):
        payload = {'title': 'A report', 'a': ['https://example.org/a.PDF?x=1',
            'https://example.org/a.PDF?x=1', 'javascript:bad.pdf', 'http://[bad'],
            'nested': {'link': 'https://example.org/report.html'}}
        wrapped = 'callback(' + json.dumps(payload) + ');'
        self.assertEqual([r['url'] for r in links.json_document_links(wrapped, 'https://example.org/api')],
                         ['https://example.org/a.PDF?x=1'])
        self.assertIsNone(links.json_body('callback({});alert("execute")'))
        self.assertIsNone(links.json_body('data = {"url":"https://example.org/a.pdf"}'))

    def test_url_builder_keeps_explicit_identity_dates_and_page(self):
        query = parse_qs(urlsplit(urls.source_url('notices', symbol='SH688178',
            start='2026-01-01', end='2026-10-19', page=2)).query)
        self.assertEqual(query['stock_list'], ['688178'])
        self.assertEqual(query['page_index'], ['2'])
        self.assertEqual(query['begin_time'], ['2026-01-01'])
        with self.assertRaises(ValueError):
            urls.source_url('finance', symbol='688178')
        with self.assertRaises(ValueError):
            urls.source_url('notices', symbol='SH688178', start='2026-10-19', end='2026-01-01')
        with self.assertRaises(ValueError):
            urls.source_url('notice', article='guess.pdf')

    def test_exact_period_identity_null_and_missing_are_not_filled(self):
        raw = json.dumps({'data': [
            {'code': 'A', 'period': '2025', 'value': 100},
            {'code': 'B', 'period': '2026', 'value': 200},
            {'code': 'A', 'period': '2026', 'value': None}]}, ensure_ascii=True)
        doc = {'sha256': 'a' * 64, 'pages': [{'page': 1, 'text': raw}]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'Ssource.json'; p.write_text(json.dumps(doc), encoding='utf8')
            result = reader.read_rows(p, {'code': 'A', 'period': '2026'}, ['value', 'absent'])
            self.assertEqual(result['rows'], [{'values': {'value': None}, 'missing_fields': ['absent']}])
            self.assertEqual(len(result['source_excerpts']), 1)
            row = result['source_excerpts'][0]
            self.assertEqual(raw[row['start']:row['end']], row['quote'])
            self.assertEqual(result['sources'][0]['quote'], row['quote'])
            self.assertEqual(reader.read_rows(p, {'code': 'C'}, ['value'])['sources'], [])


class SavedJsonPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_cannot_use_old_tool_list_to_skip_method_read(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from core.agent import Agent
        run = ResearchSession(Files(), conversation_id='test', model='unchanged', allowed=True)
        await run.begin({'scope': '研究', 'plan': ['选方法']})
        dispatch = AsyncMock(return_value={'content': 'actual'})
        agent = SimpleNamespace(router=SimpleNamespace(research=run, dispatch=dispatch),
                                audit=SimpleNamespace(record=lambda *args: None))
        calls = [{'id': 'one', 'function': {'name': 'web_fetch', 'arguments': {'url': 'https://example.org'}}}]
        events = [e async for e in Agent._execute_tools(agent, calls, 'test', [], allowed={'web_fetch'})]
        dispatch.assert_not_awaited()
        self.assertEqual(next(e for e in events if e.kind == 'tool_end').data['status'], 'error')
        await run.observe({'name': 'file_read', 'status': 'ok', 'params': {
            'path': '/workspace/skills/harness-stock-research-v3/references/event.md'}, 'result': {}})
        events = [e async for e in Agent._execute_tools(agent, calls, 'test', [], allowed={'web_fetch'})]
        dispatch.assert_awaited_once()
        self.assertEqual(next(e for e in events if e.kind == 'tool_end').data['status'], 'ok')

    async def test_method_read_precedes_fetch_compute_and_publication(self):
        run = ResearchSession(Files(), conversation_id='test', model='unchanged', allowed=True)
        await run.begin({'scope': '研究', 'plan': ['选方法', '取证']})
        self.assertTrue({'skill_info', 'file_read'}.issubset(run.tool_names))
        self.assertTrue({'web_fetch', 'code_exec', 'brief_publish'}.isdisjoint(run.tool_names))
        await run.observe({'name': 'file_read', 'status': 'error', 'params': {
            'path': '/workspace/skills/harness-stock-research-v3/references/event.md'}, 'result': {}})
        self.assertNotIn('web_fetch', run.tool_names)
        await run.observe({'name': 'file_read', 'status': 'ok', 'params': {
            'path': '/workspace/skills/harness-stock-research-v3/references/event.md'}, 'result': {'content': '原方法'}})
        self.assertTrue({'web_fetch', 'code_exec', 'brief_publish'}.issubset(run.tool_names))

    async def test_download_then_actual_read_then_referenced_calculation_and_html(self):
        files = Files()
        run = ResearchSession(files, conversation_id='test', model='unchanged', allowed=True)
        await run.begin({'scope': '已保存JSON回读', 'plan': ['读取并计算']})
        await run.observe({'name': 'file_read', 'status': 'ok', 'params': {
            'path': '/workspace/skills/harness-stock-research-v3/references/finance.md'}, 'result': {}})
        raw = json.dumps({'data': [{'code': 'A', 'period': '2025', 'unit': '元', 'amount': 1000000}]})
        source = await run.save_source({'url': 'https://example.org/data.json', 'title': '',
            'sha256': 'a' * 64, 'fetched_at': '2026-10-19', 'page_count': 1,
            'total_chars': len(raw), 'media_type': 'application/json', 'content': '[第1页]',
            '_source_file': {'base64': base64.b64encode(raw.encode()).decode(),
                             'pages': [{'page': 1, 'text': raw}]}})
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / (source['source_id'] + '.json')
            p.write_text(files.files[source['saved_source']], encoding='utf8')
            receipt = reader.read_rows(p, {'code': 'A', 'period': '2025'}, ['amount'])
        await run.begin({'phase': '核对'})
        params = {'title': '测试', 'markdown': '## 结论\n只检验材料链路。', 'as_of': '2026-10-19',
            'stance': '等待验证', 'sources': receipt['sources'], 'calculations': [
                {'operation': 'divide', 'unit': '万元', 'inputs': [
                    {'value': '1000000', 'unit': '元', 'period': '2025', 'source': source['source_id']},
                    {'value': '10000', 'unit': '元/万元', 'period': '不适用', 'source': 'constant:单位换算'}]}],
            'charts': [{'title': '量级', 'period': '2025', 'unit': '万元',
                        'items': [{'label': 'A', 'calculation': 0}]}],
            'review': {'checks': ['核对读到的原值'], 'limitations': '不认证研究结论', 'conflicts': []}}
        self.assertIn('实际收到', (await run.publish(params))['error'])
        await run.observe({'name': 'code_exec', 'status': 'ok', 'params': {},
                           'result': {'stdout': json.dumps(receipt)}})
        from core.research_review import fingerprint
        from core.research_visuals import validate_publication
        checked = validate_publication(params, run.sources, run.read_text)
        run.review_records[fingerprint(checked)]={'status':'COMPLETE','issues':[],
            'evidence_sha256':fingerprint(run.evidence_index()),'semantic_certification':False}
        published = await run.publish(params)
        self.assertEqual(published['status'], 'PUBLISHED')
        evidence = json.loads(files.files[run.record['directory'] + '/evidence.json'])
        self.assertEqual(evidence['calculations'][0]['result'], '100')
        self.assertEqual(evidence['sources'], receipt['sources'])
        self.assertIn('<svg', files.files[published['path']])
        self.assertEqual(evidence['markdown'], params['markdown'])
