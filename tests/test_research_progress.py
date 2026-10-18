import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_research_session as fixtures


class ResearchProgressTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self):
        result = await fixtures.ResearchSessionTests().prepare()
        result[1]._observed_milestones = len(result[1]._milestones)
        return result

    async def test_different_prints_and_repeated_phase_do_not_reset_stagnation(self):
        _, run, _ = await self.prepare()
        for i in range(14):
            await run.observe({'name':'code_exec', 'status':'ok', 'params':{},
                               'result':{'stdout':str(i), 'exit_code':0}})
        self.assertEqual(run.steps_without_progress, 14)
        self.assertIn('14步', run.guidance())
        event = {'name':'research_begin', 'status':'ok', 'params':{}, 'result':{}}
        await run.observe(event)
        self.assertEqual(run.steps_without_progress, 0)
        await run.observe(event)
        self.assertEqual(run.steps_without_progress, 1)

    async def test_exact_delivered_excerpts_only_and_snapshot_restoration(self):
        files, run, params = await self.prepare()
        key = params['sources'][0]['id']
        # Downloaded verification text is not yet AI reading.
        saved = json.loads(files.files[run.sources[key]['path']])
        saved['pages'].append({'page':2, 'text':'尚未签订正式合同。收入确认时间不确定。'})
        from core.research_session import encoded
        from hashlib import sha256
        files.files[run.sources[key]['path']] = encoded(saved)
        run.sources[key]['artifact_sha256'] = sha256(encoded(saved).encode()).hexdigest()
        run._source_pages.clear()  # same path as manual continuation
        self.assertNotIn('尚未签订', str(run.read_text))
        row = {'source_id': key, 'sha256':'a'*64, 'page':2, 'start':0, 'end':9, 'quote':'尚未签订正式合同。'}
        event = {'name':'code_exec', 'status':'ok', 'params':{},
                 'result':{'exit_code':0, 'stdout':json.dumps({'source_excerpts':[row]}, ensure_ascii=False)}}
        await run.observe(event)
        self.assertIn(row['quote'], run.read_text[key])
        self.assertNotIn(saved['pages'][1]['text'], run.read_text[key])
        self.assertEqual(run.steps_without_progress, 0)
        await run.observe(event)
        self.assertEqual(run.steps_without_progress, 1)
        before = copy.deepcopy(run.read_text)
        for field, bad in [('source_id','invented'), ('sha256','b'*64), ('page',3),
                           ('quote','已经签订正式合同。'), ('end',False)]:
            changed = {**row, field:bad}
            await run.source_excerpts({'stdout':json.dumps({'source_excerpts':[changed]})})
        self.assertEqual(run.read_text, before)

    async def test_failed_execution_cannot_register_excerpts(self):
        _, run, _ = await self.prepare()
        before = copy.deepcopy(run.read_text)
        await run.observe({'name':'code_exec','status':'error','params':{},
                           'result':{'stdout':'{"source_excerpts":[]}', 'exit_code':1}})
        self.assertEqual(before, run.read_text)
        self.assertEqual(run.steps_without_progress, 1)

    def test_batch_script_preserves_page_offsets_and_missing_terms(self):
        path = Path('skills/runner/packages/harness-stock-research-v2/scripts/extract.py')
        spec = importlib.util.spec_from_file_location('research_extract', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with TemporaryDirectory(dir='data/tmp') as directory:
            source = Path(directory)/'Sactual.json'
            pages = [{'page':1,'text':'单位：元\n营业收入100元\n归属于母公司股东的利润20元'},
                     {'page':2,'text':'风险：尚未签约'}]
            source.write_text(json.dumps({'sha256':'a'*64,'pages':pages}),encoding='utf-8')
            result = module.extract(source, ['营业收入','尚未签约','不存在'], context=40)
            self.assertEqual(len(result['source_excerpts']),2)
            self.assertEqual(result['unmatched_terms'],['不存在'])
            for row in result['source_excerpts']:
                self.assertEqual(row['quote'],pages[row['page']-1]['text'][row['start']:row['end']])
