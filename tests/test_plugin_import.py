"""Real file import/update preserves fixed bytes without executing package code."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from core.conversation_store import ConversationStore
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_packages import PluginPackages


class PluginImportFlowTests(unittest.TestCase):
    def test_directory_zip_update_history_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample'
            source.mkdir()
            metadata = dict(id='sample', version='1.0', title='Example', description='fixture',
                harness_api=1, backends=['sample'], required=[], optional=[], permissions=[],
                execution='container', tool_registration='managed')
            (source / 'plugin.json').write_text(json.dumps(metadata))
            (source / 'module.py').write_text("raise RuntimeError('must never execute during import')")
            store = CapabilityStore(ConversationStore(root / 'conversations.db')._connect)
            packages = PluginPackages(store, root / 'packages', root / 'bundled')
            first = packages.import_source(source)
            revision = first['pending']['revision']
            self.assertIsNone(first['active'])
            self.assertEqual(packages.import_source(source)['pending']['revision'], revision)
            (source / 'module.py').write_text('# updated')
            archive = root / 'sample.zip'
            with zipfile.ZipFile(archive, 'w') as handle:
                for file in source.iterdir():
                    handle.write(file, file.name)
            second = packages.import_source(archive)
            self.assertNotEqual(second['pending']['revision'], revision)
            old = packages.read('sample', revision)
            self.assertIn('must never execute', (old / 'module.py').read_text())
            (old / 'module.py').write_text('tampered')
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
