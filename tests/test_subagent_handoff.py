import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.batch import BatchRunner
from harness.runtime.subagents.materials import read_material, role_prompt
from harness.runtime.subagents.results import structured_result
from core.subagent_executor import AgentSubagentExecutor
from tests.subagent_fixtures import MemoryStore, role, complete_result


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_evidence_reads_all_pages_and_rejects_bad_cursor(self):
        reads = []
        def reader(key, cursor=0):
            reads.append(cursor)
            return {'content': 'abcdefgh'[cursor:cursor + 3],
                    'next_cursor': cursor + 3 if cursor < 6 else None}
        self.assertEqual(read_material(reader, 'e')['content'], 'abcdefgh')
        self.assertEqual(reads, [0, 3, 6])
        with self.assertRaises(ValueError):
            read_material(lambda *args: {'content': 'x', 'next_cursor': 0}, 'e')

    def test_real_shape_failures_stay_failures_fence_only_is_lossless(self):
        from dataclasses import replace
        spec = replace(role('calculation', kind='calculation'),
                       output_schema={'type':'object', 'required':['calculations','figures']})
        task = RoleTask(role=spec, text='calculate')
        valid = '{"artifacts":[{"kind":"calculation","content":{"calculations":[],"figures":[]}}]}'
        result = structured_result(task, '```json\n' + valid + '\n```', status='COMPLETE')
        self.assertEqual(result.raw_text, '```json\n' + valid + '\n```')
        for invalid in (valid[:-4], valid.replace(',"figures":[]', ''), '{"facts":[]}'):
            with self.assertRaises(ValueError):
                structured_result(task, invalid, status='COMPLETE')
        self.assertIn('figures', role_prompt(spec))

    def test_real_fenced_preamble_preserves_payload_and_entire_visible_receipt(self):
        from dataclasses import replace
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'finance_fenced_preamble.json').read_text(encoding='utf-8'))
        spec = replace(role('finance_evidence', kind='source_excerpts'),
                       output_schema={'type':'object', 'required':['excerpts']})
        task = RoleTask(role=spec, text='read the saved original')
        raw = fixture['raw_text']
        result = structured_result(task, raw, status='COMPLETE')
        self.assertEqual(result.raw_text, raw)
        self.assertEqual(result.artifacts[0].content, fixture['content'])
        self.assertEqual(result.issues, tuple({'reason':x} for x in fixture['issues']))
        failed = structured_result(task, raw, status='UNKNOWN', error='network unknown')
        self.assertEqual(failed.status, 'UNKNOWN')
        self.assertEqual(failed.artifacts, ())
        self.assertEqual(failed.raw_text, raw)

    def test_fence_extraction_rejects_ambiguity_truncation_and_invalid_contract(self):
        from dataclasses import replace
        spec = replace(role('calculation', kind='calculation'),
                       output_schema={'type':'object', 'required':['calculations','figures']})
        task = RoleTask(role=spec, text='calculate')
        valid = '{"artifacts":[{"kind":"calculation","content":{"calculations":[],"figures":[]}}]}'
        fenced = '```json\n' + valid + '\n```'
        for invalid in (fenced + '\n' + fenced, '说明\n' + fenced[:-3],
                        '说明\n```json\n' + valid[:-3] + '\n```',
                        '说明\n' + fenced.replace(',"figures":[]', ''),
                        '说明\n' + fenced.replace('"kind":"calculation"', '"kind":"claims"'),
                        '说明\n' + valid):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    structured_result(task, invalid, status='COMPLETE')

    async def test_invalid_evidence_does_not_start_any_model(self):
        def denied(key):
            raise ValueError('not authorized')
        executor = AgentSubagentExecutor(SimpleNamespace(
            registry=SimpleNamespace(known_tools=set()), context_mgr=SimpleNamespace(read_evidence=denied)))
        with self.assertRaisesRegex(ValueError, 'not authorized'):
            executor.validate((RoleTask(role=role('a'), text='x', evidence_ids=('Ssource',)),))

    async def test_stop_waits_for_cleanup_and_preserves_unconfirmed_call(self):
        started, cleanup = asyncio.Event(), asyncio.Event()
        class Executor:
            async def run(self, task, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(.01)
                    cleanup.set()
        runner = BatchRunner(Executor(), MemoryStore())
        batch = runner.create((RoleTask(role=role('a'), text='x'),
                               RoleTask(role=role('b', depends_on=('a',)), text='y')))
        await started.wait()
        result = await runner.stop(batch['batch_id'])
        self.assertTrue(cleanup.is_set())
        self.assertEqual(result['results']['a']['status'], 'UNKNOWN')
        self.assertEqual(result['results']['b']['status'], 'STOPPED')
        self.assertFalse(result['execution_active'])

    async def test_new_runner_reads_stored_batch_without_execution(self):
        store = MemoryStore()
        store.create_batch('saved', (RoleTask(role=role('a'), text='x'),))
        store.save_result('saved', complete_result('a'))
        runner = BatchRunner(None, store)
        result = await runner.result('saved')
        self.assertTrue(result['complete'])
        self.assertFalse(result['execution_active'])
        self.assertEqual(result['results']['a']['artifacts'][0]['content'], 'ok')

    async def test_mapping_failure_prevents_dependent_execution(self):
        calls = []
        class Executor:
            async def run(self, task, context):
                calls.append(task.role.role_id)
                return complete_result(task.role.role_id)
        async def reject(batch, result):
            raise ValueError('mapping failed')
        runner = BatchRunner(Executor(), MemoryStore(), result_handler=reject)
        batch = runner.create((RoleTask(role=role('a'), text='x'),
                              RoleTask(role=role('b', depends_on=('a',)), text='y')))
        with self.assertRaisesRegex(ValueError, 'mapping failed'):
            await runner.result(batch['batch_id'])
        self.assertEqual(calls, ['a'])

    async def test_observer_cancellation_does_not_cancel_execution(self):
        go = asyncio.Event()
        class Executor:
            async def run(self, task, context):
                await go.wait()
                return complete_result(task.role.role_id)
        runner = BatchRunner(Executor(), MemoryStore())
        batch = runner.create((RoleTask(role=role('a'), text='x'),))
        observer = asyncio.create_task(runner.result(batch['batch_id']))
        await asyncio.sleep(0)
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        go.set()
        self.assertTrue((await runner.result(batch['batch_id']))['complete'])
