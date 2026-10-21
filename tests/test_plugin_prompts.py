"""Install/remove declarations, disable tools, and reject escaping prompt files."""
import json
from pathlib import Path
import tempfile
import unittest

from core.prompt_builder import PromptBuilder
from harness.capabilities.prompts import load_prompts


class PluginPromptFlowTests(unittest.TestCase):
    def test_declaration_controls_context_without_changing_host_and_tracks_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modules, packages = root / 'modules', root / 'plugins'
            modules.mkdir(); package = packages / 'documents'; package.mkdir(parents=True)
            (modules / 'base.txt').write_text('普通聊天规则', encoding='utf-8')
            source = package / 'instructions.txt'
            source.write_text('文档处理规则', encoding='utf-8')
            manifest = package / 'plugin.json'
            metadata = {'id': 'documents', 'harness_api': 1, 'prompts': [
                {'path': 'instructions.txt', 'when_tools': ['document_process']}]}
            manifest.write_text(json.dumps(metadata), encoding='utf-8')
            active = set()
            builder = PromptBuilder(modules_dir=modules, packages_dir=packages,
                                    registered_tools=lambda: frozenset(active))
            self.assertNotIn('文档处理规则', builder.build())
            self.assertIn('普通聊天规则', builder.build())
            self.assertIn('文档处理规则', builder.build(available_tools={'document_process'}))
            active.add('document_process')
            self.assertIn('文档处理规则', builder.build())
            active.clear()
            self.assertNotIn('文档处理规则', builder.build())
            before = load_prompts(packages)[0][0].fingerprint
            source.write_text('新版文档处理规则', encoding='utf-8')
            self.assertNotEqual(before, load_prompts(packages)[0][0].fingerprint)
            # An already constructed runtime is stable until normal deployment.
            self.assertNotIn('新版', builder.build(available_tools={'document_process'}))
            metadata['prompts'][0]['path'] = '../../modules/base.txt'
            manifest.write_text(json.dumps(metadata), encoding='utf-8')
            invalid = PromptBuilder(modules_dir=modules, packages_dir=packages)
            self.assertIn('documents', invalid.contribution_errors)
            self.assertEqual(invalid.build(available_tools={'document_process'}).count('普通聊天规则'), 1)
            manifest.unlink()
            self.assertEqual(load_prompts(packages), ([], {}))
