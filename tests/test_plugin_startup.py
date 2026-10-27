"""Startup restores cross-environment dependencies after all registrations."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.conversation_store import ConversationStore
from harness.capabilities.startup import restore_plugins
from harness.capabilities.catalog import package_catalog
from harness.infrastructure.capability_store import CapabilityStore


class StartupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_environment_order_and_optional_failure_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SimpleNamespace(capabilities=None, router=SimpleNamespace(),
                                      tool_registry=None, store=ConversationStore(root / 'state.db'))
            events = []

            class Provider:
                def __init__(self, root, metadata, *args):
                    self.identity = metadata['id']

                async def activate(self):
                    events.append(self.identity)

                async def deactivate(self):
                    pass

            declarations = [
                ('a-files', 'files', ['b-container']),
                ('b-container', 'container', ['z-container']),
                ('z-container', 'container', []),
                ('broken', 'container', ['missing']),
                ('independent', 'files', []),
                ('blocked', 'files', ['disabled']),
                ('disabled', 'files', []),
            ]
            store = CapabilityStore(runtime.store._connect)
            for identity, execution, required in declarations:
                package = root / identity
                package.mkdir()
                (package / 'plugin.json').write_text(json.dumps(dict(id=identity,
                    execution=execution, required=required, harness_api=1,
                    version='1.0', title=identity, description='fixture',
                    backends=[identity], optional=[], permissions=[],
                    tool_registration='managed')), encoding='utf-8')
                store.save('__host__', identity, {'enabled': identity != 'disabled'}, 'plugin')
            invalid = root / 'invalid'
            invalid.mkdir()
            (invalid / 'plugin.json').write_text(json.dumps(dict(id='invalid',
                harness_api=True, execution='container', tool_registration='managed')))
            store.save('__host__', 'invalid', {'enabled': True}, 'plugin')
            with patch('harness.capabilities.packaged_plugins.PackagedProvider', Provider), \
                    patch('harness.capabilities.container_plugins.ContainerPackage', Provider):
                await restore_plugins(runtime, root, 'http://unused', {})
            self.assertEqual(events[:3], ['z-container', 'b-container', 'a-files'])
            self.assertEqual(events.count('z-container'), 1)
            self.assertEqual(runtime.capabilities.status('independent')['state'], 'ACTIVE')
            broken = runtime.capabilities.status('broken')
            self.assertEqual(broken['state'], 'START_FAILED')
            self.assertIn('missing', broken['error'])
            self.assertFalse(runtime.capabilities.manages('invalid'))
            self.assertEqual(runtime.capabilities.status('disabled')['state'], 'INACTIVE')
            self.assertEqual(runtime.capabilities.status('blocked')['state'], 'START_FAILED')
            self.assertNotIn('disabled', events)
            self.assertIn('明确启用', runtime.capabilities.status('blocked')['error'])
            rows = {row['id']: row for row in package_catalog(root, {})}
            self.assertEqual(rows['invalid']['registration'], 'invalid')
            self.assertIn('API', rows['invalid']['error'])
            self.assertEqual(rows['a-files']['management'], 'managed')

    async def test_manifest_adapter_is_registered_before_restore(self):
        import os
        import sys
        import types
        from harness.runtime.lifecycle import LifecycleManager
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'demo'
            package.mkdir()
            (package / 'plugin.json').write_text(json.dumps({
                'id': 'demo', 'harness_api': 1, 'version': '1', 'title': 'Demo',
                'description': 'fixture', 'backends': ['demo'], 'required': [],
                'optional': [], 'permissions': [], 'adapter': 'plugins.demo.service:service'}), encoding='utf-8')
            events = []

            class Provider:
                async def activate(self): events.append('activate')
                async def deactivate(self): pass

            class Service:
                def lifecycle(self, runtime):
                    events.append('register')
                    runtime.capabilities = LifecycleManager()
                    runtime.capabilities.register('demo', Provider())

                async def restore(self, runtime):
                    events.append('restore')
                    await runtime.capabilities.activate('demo')

            module = types.ModuleType('plugins.demo.service')
            module.service = Service()
            runtime = SimpleNamespace(capabilities=None, router=SimpleNamespace(),
                                      tool_registry=None, store=ConversationStore(root / 'state.db'))
            with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
                    patch.dict(sys.modules, {'plugins.demo.service': module}):
                await restore_plugins(runtime, root, 'http://unused', {})
            self.assertEqual(events, ['register', 'restore', 'activate'])
            self.assertIs(runtime.capability_services['demo'], module.service)
            self.assertEqual(runtime.capabilities.status('demo')['state'], 'ACTIVE')
            rows = package_catalog(root, {})
            self.assertEqual(rows[0]['management'], 'managed_service')
