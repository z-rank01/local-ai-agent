import asyncio
import unittest
from types import SimpleNamespace
import httpx
from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.batch import BatchRunner
from tests.subagent_fixtures import MemoryStore, role, complete_result


class SubagentBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_roles_run_and_dependency_waits(self):
        calls = []
        class Executor:
            async def run(self, task, context):
                calls.append(('start', task.role.role_id))
                await asyncio.sleep(0.01)
                calls.append(('end', task.role.role_id))
                return complete_result(task.role.role_id)
        tasks = tuple(RoleTask(role=role, text=role.role_id) for role in
                      (role('evidence'), role('other'), role('analysis',depends_on=('evidence',))))
        runner = BatchRunner(Executor(), MemoryStore())
        created = runner.create(tasks)
        result = await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertTrue(result['complete'])
        self.assertEqual(set(result['results']), {'evidence','other','analysis'})
        self.assertLess(calls.index(('end','evidence')), calls.index(('start','analysis')))

    async def test_failed_parent_skips_dependency_without_retry(self):
        calls = []
        class Executor:
            async def run(self, task, context):
                calls.append(task.role.role_id)
                if task.role.role_id == 'evidence':
                    raise RuntimeError('failed')
                return complete_result(task.role.role_id)
        tasks = (RoleTask(role=role('evidence'), text='取证'),
                 RoleTask(role=role('analysis',depends_on=('evidence',)), text='分析'))
        runner = BatchRunner(Executor(), MemoryStore())
        created = runner.create(tasks)
        result = await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertEqual(result['results']['evidence']['status'], 'FAILED')
        self.assertEqual(result['results']['analysis']['status'], 'FAILED')
        self.assertEqual(calls, ['evidence'])

    async def test_parent_completion_stops_waiting_roles(self):
        parent = asyncio.create_task(asyncio.sleep(100))
        control = SimpleNamespace(task=parent)
        class Executor:
            async def run(self, task, context):
                await asyncio.Event().wait()
        runner = BatchRunner(Executor(), MemoryStore(), parent_control=control)
        created = runner.create((RoleTask(role=role('evidence'), text='取证'),))
        parent.cancel()
        result = await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertEqual(result['results']['evidence']['status'], 'STOPPED')

    async def test_transport_error_is_unknown_once(self):
        calls = []
        class Executor:
            async def run(self, task, context):
                calls.append(task.role.role_id)
                raise httpx.ConnectError('offline')
        runner = BatchRunner(Executor(), MemoryStore())
        created = runner.create((RoleTask(role=role('evidence'), text='取证'),))
        result = await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertEqual(result['results']['evidence']['status'], 'UNKNOWN')
        self.assertEqual(calls, ['evidence'])

    async def test_mapping_failure_releases_batch_waiter(self):
        async def failed_mapping(batch_id, result):
            raise ValueError('mapping failed')
        runner = BatchRunner(type('Executor', (), {'run':lambda self, task, context:
            complete_result(task.role.role_id)})(), MemoryStore(), result_handler=failed_mapping)
        created = runner.create((RoleTask(role=role('evidence'), text='取证'),))
        with self.assertRaisesRegex(ValueError, 'mapping failed'):
            await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertTrue(runner._events[created['batch_id']].is_set())

    async def test_save_failure_does_not_leave_batch_pending(self):
        class BrokenStore(MemoryStore):
            def save_result(self, batch_id, result):
                raise RuntimeError('save failed')
        runner = BatchRunner(type('Executor', (), {'run':lambda self, task, context:
            complete_result(task.role.role_id)})(), BrokenStore())
        created = runner.create((RoleTask(role=role('evidence'), text='取证'),))
        with self.assertRaisesRegex(RuntimeError, 'save failed'):
            await asyncio.wait_for(runner.result(created['batch_id']), 1)
        self.assertTrue(runner._events[created['batch_id']].is_set())


if __name__ == '__main__':
    unittest.main()
