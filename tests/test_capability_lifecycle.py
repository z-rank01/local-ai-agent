"""Functional dependency activation, concurrent drain and failure preservation."""
import asyncio
import unittest
from harness.runtime.lifecycle import LifecycleManager


class Provider:
    def __init__(self, name, events):
        self.name, self.events = name, events
        self.fail_stop = False

    async def activate(self):
        self.events.append('start:' + self.name)

    async def deactivate(self):
        if self.fail_stop:
            raise RuntimeError('resource still running')
        self.events.append('stop:' + self.name)


class LifecycleFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_order_and_stop_drains_inflight_and_refuses_new_calls(self):
        events = []
        manager = LifecycleManager()
        manager.register('index', Provider('index', events))
        manager.register('compare', Provider('compare', events), required=['index'])
        await manager.activate('compare')
        self.assertEqual(events, ['start:index', 'start:compare'])
        with self.assertRaisesRegex(ValueError, '先停用'):
            await manager.deactivate('index')
        entered, finish = asyncio.Event(), asyncio.Event()
        async def invocation():
            async with manager.call('compare'):
                entered.set()
                await finish.wait()
                return 'actual result'
        call = asyncio.create_task(invocation())
        await entered.wait()
        stop = asyncio.create_task(manager.deactivate('compare'))
        await asyncio.sleep(0)
        self.assertEqual(manager.status('compare')['state'], 'DRAINING')
        with self.assertRaisesRegex(RuntimeError, '不接收'):
            async with manager.call('compare'):
                self.fail('must not admit')
        self.assertFalse(stop.done())
        finish.set()
        self.assertEqual(await call, 'actual result')
        await stop
        await manager.deactivate('index')
        self.assertEqual(events[-2:], ['stop:compare', 'stop:index'])

    async def test_failed_stop_preserves_state_and_dependency_failures_are_isolated(self):
        events = []
        manager = LifecycleManager()
        provider = Provider('good', events)
        manager.register('good', provider)
        manager.register('broken', Provider('broken', events), required=['missing'])
        with self.assertRaisesRegex(ValueError, '缺少'):
            await manager.activate('broken')
        await manager.activate('good')
        provider.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            await manager.deactivate('good')
        self.assertEqual(manager.status('good')['state'], 'STOP_FAILED')
        self.assertFalse(manager.status('good')['accepting'])
        with self.assertRaisesRegex(ValueError, '先完成'):
            await manager.activate('good')
        provider.fail_stop = False
        await manager.deactivate('good')
        self.assertEqual(manager.status('good')['state'], 'INACTIVE')
        self.assertEqual(manager.status('broken')['state'], 'START_FAILED')
