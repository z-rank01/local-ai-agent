"""Registered service preparation generates isolated, optional launcher topology."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import config
from harness.infrastructure.plugin_services import service_spec
from scripts import plugin_service, workspace_compose


class PluginServiceTopologyTests(unittest.TestCase):
    def test_registered_ids_create_stable_binding_without_starting_a_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'plugins/documents'
            (package / 'runtime').mkdir(parents=True)
            (package / 'runtime/Dockerfile').write_text('FROM example\n', encoding='utf-8')
            manifest = {'id': 'documents', 'harness_api': 1, 'version': '1.0.0',
                        'services': {'ocr': {'port': 8300, 'dockerfile': 'runtime/Dockerfile'}}}
            path = package / 'plugin.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            (root / 'docker-compose.yml').write_text(json.dumps({'services': {
                'skill-files': {'environment': {}}, 'skill-runner': {'environment': {}}},
                'networks': {}, 'volumes': {}}), encoding='utf-8')
            with patch.object(config, 'PROJECT_ROOT', root), patch.object(config, 'DB_PATH', root / 'conversations.db'), \
                    patch.object(config, 'WORKSPACE_PATH', root / 'workspace'), \
                    patch.object(plugin_service, 'ROOT', root), patch.object(workspace_compose, 'root', root):
                first = plugin_service.resolve('documents', 'ocr', 'default', 'prepare')
                second = plugin_service.resolve('documents', 'ocr', 'default', 'start')
                self.assertEqual(first, second)
                topology = json.loads((root / 'data/private/workspace-compose.json').read_text())
                service = topology['services'][first['name']]
                self.assertEqual(service['profiles'], ['plugin-services'])
                self.assertNotIn('ports', service)
                gateway = topology['services'][first['name'] + '-gateway']
                self.assertEqual(gateway['ports'], [f'127.0.0.1:{first["port"]}:8300'])
                self.assertEqual(gateway['environment']['UPSTREAM_HOST'], first['name'])
                self.assertEqual(gateway['environment']['UPSTREAM_PORT'], '8300')
                self.assertEqual(len(gateway['volumes']), 1)
                self.assertEqual(gateway['volumes'][0]['target'], '/service_proxy.py')
                self.assertTrue(gateway['volumes'][0]['read_only'])
                self.assertNotIn('WORKSPACE_TOOL_TOKEN', gateway['environment'])
                self.assertEqual(service['environment']['WORKSPACE_ID'], 'default')
                self.assertEqual(service['cap_drop'], ['ALL'])
                self.assertTrue(topology['networks'][service['networks'][0]]['internal'])
                self.assertNotIn('command', service)
                self.assertEqual([mount['source'] for mount in service['volumes'] if 'source' in mount],
                                 [str(root / 'workspace')])
                self.assertEqual(service['build']['labels']['local-ai-agent.revision'], first['revision'])
                (package / 'runtime/requirements.txt').write_text('package==1\n', encoding='utf-8')
                self.assertNotEqual(first['revision'], service_spec(root, 'documents', 'ocr')['revision'])
                with self.assertRaises(ValueError): service_spec(root, '../documents', 'ocr')
                with self.assertRaises(ValueError): service_spec(root, 'documents', 'missing')
                manifest['services']['ocr']['command'] = 'not allowed'
                path.write_text(json.dumps(manifest), encoding='utf-8')
                with self.assertRaises(ValueError): service_spec(root, 'documents', 'ocr')
