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
                store.save('__host__', identity, {'enabled': True}, 'plugin')
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
            rows = {row['id']: row for row in package_catalog(root, {})}
            self.assertEqual(rows['invalid']['registration'], 'invalid')
            self.assertIn('API', rows['invalid']['error'])
            self.assertEqual(rows['a-files']['management'], 'managed')
