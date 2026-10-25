"""Real file import/update preserves fixed bytes without executing package code."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from core.conversation_store import ConversationStore
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_packages import PluginPackages
from harness.infrastructure.plugin_deployment import apply_pending, confirm_build
from harness.infrastructure.plugin_recovery import restore_before_build
from harness.infrastructure.package_diff import package_diff


def payload(source):
    (source / 'runtime').mkdir(exist_ok=True)
    (source / 'runtime/provider.py').write_text('# fixture provider; import must not execute it')
    (source / 'tools').mkdir(exist_ok=True)
    (source / 'tools/sample.yaml').write_text('name: sample\nbackend: skill-files\nparameters: {type: object}\n')


class PluginImportFlowTests(unittest.TestCase):
    def test_directory_zip_update_history_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample'
            source.mkdir()
            metadata = dict(id='sample', version='1.0', title='Example', description='fixture',
                harness_api=1, backends=['sample'], required=[], optional=[], permissions=[],
                execution='files', tool_registration='managed')
            (source / 'plugin.json').write_text(json.dumps(metadata))
            payload(source)
            (source / 'runtime/provider.py').write_text("raise RuntimeError('must never execute during import')")
            store = CapabilityStore(ConversationStore(root / 'conversations.db')._connect)
            packages = PluginPackages(store, root / 'packages', root / 'bundled')
            first = packages.import_source(source)
            revision = first['pending']['revision']
            self.assertIsNone(first['active'])
            self.assertEqual(packages.import_source(source)['pending']['revision'], revision)
            (source / 'runtime/provider.py').write_text('# updated')
            archive = root / 'sample.zip'
            with zipfile.ZipFile(archive, 'w') as handle:
                for file in source.rglob('*'):
                    if file.is_file(): handle.write(file, file.relative_to(source).as_posix())
            second = packages.import_source(archive)
            self.assertNotEqual(second['pending']['revision'], revision)
            old = packages.read('sample', revision)
            self.assertIn('must never execute', (old / 'runtime/provider.py').read_text())
            (old / 'runtime/provider.py').write_text('tampered')
            with self.assertRaisesRegex(ValueError, '不一致'):
                packages.read('sample', revision)
            before = store.list('__host__', 'plugin_package')
            for invalid in ('../outside.py', 'Module.py'):
                with zipfile.ZipFile(archive, 'w') as handle:
                    handle.writestr('plugin.json', json.dumps(metadata))
                    handle.writestr('module.py', 'x')
                    handle.writestr(invalid, 'x')
                with self.assertRaises(ValueError):
                    packages.import_source(archive)
            self.assertFalse((root / 'outside.py').exists())
            self.assertEqual(before, store.list('__host__', 'plugin_package'))

    def test_reviewed_deploy_update_rollback_and_uninstall_keep_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'; source.mkdir()
            metadata = dict(id='sample', version='1.0', title='Example', description='fixture',
                harness_api=1, backends=['skill-files'], required=[], optional=[], permissions=[],
                execution='files', tool_registration='managed')
            (source / 'plugin.json').write_text(json.dumps(metadata))
            payload(source)
            store = CapabilityStore(ConversationStore(root / 'conversations.db')._connect)
            packages = PluginPackages(store, root / 'saved', root / 'plugins')
            first = packages.import_source(source)['pending']['revision']
            self.assertEqual(apply_pending(packages)['changed'], [])
            with self.assertRaisesRegex(ValueError, '信任'):
                packages.queue('sample', 'activate', first)
            packages.queue('sample', 'activate', first, trusted=True)
            self.assertTrue(apply_pending(packages)['build_required'])
            self.assertTrue((root / 'plugins/sample/plugin.json').exists())
            confirm_build(packages)
            self.assertFalse(store.list('__host__', 'plugin')['sample']['enabled'])
            unchanged = packages.import_source(source)
            self.assertIsNone(unchanged['pending'])
            self.assertEqual(unchanged['active']['revision'], first)
            self.assertEqual(unchanged['deployment'], 'INSTALLED')
            metadata['version'] = '2.0'
            (source / 'plugin.json').write_text(json.dumps(metadata))
            payload(source)
            second = packages.import_source(source)['pending']['revision']
            differences = package_diff(packages, 'sample', second)
            self.assertEqual(differences['files'][0]['path'], 'plugin.json')
            self.assertIn('2.0', differences['files'][0]['diff'])
            packages.queue('sample', 'activate', second, trusted=True)
            apply_pending(packages)
            restored = restore_before_build(packages, 'fixture compiler rejected candidate')
            self.assertEqual(restored['restored'], ['sample'])
            recovered = store.list('__host__', 'plugin_package')['sample']
            self.assertEqual(recovered['active']['revision'], first)
            self.assertEqual(recovered['pending']['revision'], second)
            self.assertEqual(recovered['deployment'], 'BUILD_FAILED')
            self.assertEqual(json.loads((root / 'plugins/sample/plugin.json').read_text())['version'], '1.0')
            packages.queue('sample', 'activate', second, trusted=True)
            apply_pending(packages); confirm_build(packages)
            packages.queue('sample', 'rollback', first, trusted=True)
            self.assertFalse(apply_pending(packages)['errors'])
            confirm_build(packages)
            self.assertEqual(json.loads((root / 'plugins/sample/plugin.json').read_text())['version'], '1.0')
            packages.queue('sample', 'uninstall')
            self.assertFalse(apply_pending(packages)['errors'])
            confirm_build(packages)
            self.assertFalse((root / 'plugins/sample').exists())
            self.assertTrue(packages.read('sample', second).exists())


class PluginImportApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_import_review_and_stage_do_not_execute_or_replace_live_code(self):
        import httpx
        from unittest.mock import patch
        from types import SimpleNamespace
        from bff.app import app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample'; source.mkdir()
            (source / 'plugin.json').write_text(json.dumps(dict(id='sample', version='1',
                title='Example', description='fixture', harness_api=1, backends=['skill-files'],
                required=[], optional=[], permissions=[], execution='files', tool_registration='managed')))
            payload(source)
            runtime = SimpleNamespace(store=ConversationStore(root / 'conversations.db'))
            with patch('bff.plugin_packages.get_runtime', return_value=runtime), \
                    patch('bff.plugin_packages.config._PROJECT_ROOT', root), \
                    patch('core.ui_session.session_token', return_value='test-page-token'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        base_url='http://127.0.0.1:9510', headers={'X-Harness-Session':'test-page-token'}) as client:
                    denied = await client.post('/api/plugin-packages/import', json={'path':str(source)})
                    self.assertEqual(denied.status_code, 403)
                    client.headers['Origin'] = 'http://127.0.0.1:9510'
                    provider = source / 'runtime/provider.py'
                    provider.unlink()
                    invalid = await client.post('/api/plugin-packages/import', json={'path':str(source)})
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(CapabilityStore(runtime.store._connect).list('__host__', 'plugin_package'), {})
                    payload(source)
                    imported = await client.post('/api/plugin-packages/import', json={'path':str(source)})
                    self.assertEqual(imported.status_code, 200)
                    revision = imported.json()['pending']['revision']
                    action = {'action':'activate', 'revision':revision}
                    self.assertEqual((await client.post('/api/plugin-packages/sample', json=action)).status_code, 409)
                    action['trusted'] = True
                    queued = await client.post('/api/plugin-packages/sample', json=action)
                    self.assertEqual(queued.json()['deployment'], 'PENDING_RESTART')
                    self.assertFalse((root / 'plugins/sample').exists())
                    diff = await client.get('/api/plugin-packages/sample/diff', params={'after':revision})
                    self.assertEqual(diff.status_code, 200)
                    self.assertEqual(diff.json()['files'][0]['change'], 'added')
