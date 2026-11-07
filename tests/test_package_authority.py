import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.package_management import PackageManagement
from core.conversation_store import ConversationStore
from harness.contracts.tool_provider import ExecutionContext
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.package_authority import signed_message
from harness.infrastructure.plugin_archive import content_revision, read_package
from tests.test_plugin_import import payload


class PackageAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_skill_uses_pinned_snapshot_and_preserves_it_after_uninstall(self):
        from harness.capabilities.skill_catalog import SkillCatalog
        from unittest.mock import AsyncMock, Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SimpleNamespace(store=ConversationStore(root / 'chat.db'), capabilities=None,
                tool_registry=SimpleNamespace(ownership=lambda: {}, hidden_research_methods=lambda: set()))
            records = CapabilityStore(runtime.store._connect)
            entry = dict(name='method', revision='a'*64, permissions=['workspace.read'], kind='skill_md')
            frozen = {'versions': {}, 'invalid': []}
            runner = AsyncMock(side_effect=[frozen, entry])
            context = ExecutionContext('w', root, lambda _: root,
                permissions=frozenset({'workspace.write', 'workspace.read'}), runner_operation=runner)
            transport = PackageManagement(runtime, context)
            request = dict(kind='skill', identity='method', action='install', path='/workspace/method')
            with self.assertRaises(PermissionError):
                await transport.call_tool('capability_package', request, 's')
            records.save('w', 'method', dict(actions=['install','uninstall'], permissions=['workspace.read']),
                         'skill_source_grant')
            check = Mock()
            with patch('core.package_management.verify_package',check):
                result = await transport.call_tool('capability_package', request, 's')
            check.assert_called_once()
            self.assertEqual(check.call_args.args[1:3], ('skill/method', 'a'*64))
            self.assertEqual(result['status'], 'ACTIVE_FOR_NEW_RUNS')
            catalog = SkillCatalog(records, 'w', [], context.permissions)
            captured = catalog.effective(frozen)
            self.assertEqual(captured['versions']['method']['revision'], 'a'*64)
            await transport.call_tool('capability_package', {**request,'action':'uninstall'}, 's')
            self.assertEqual(catalog.effective(frozen)['versions'], {})
            self.assertEqual(captured['versions']['method']['revision'], 'a'*64)

    async def test_signed_revision_cannot_escalate_or_activate_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / 'sample'
            src.mkdir()
            metadata = dict(id='sample', version='1.0', title='fixture', description='fixture',
                harness_api=1, backends=['sample'], required=[], optional=[], permissions=[],
                execution='files', tool_registration='managed')
            manifest = src / 'plugin.json'
            manifest.write_text(json.dumps(metadata))
            payload(src)
            revision = content_revision(read_package(src))
            openssl = Path(sys.prefix) / 'Library/bin/openssl.exe'

            def openssl_call(*args):
                return subprocess.run([str(openssl), *args], cwd=root, check=True,
                                      capture_output=True, timeout=10).stdout

            openssl_call('genpkey', '-algorithm', 'Ed25519', '-out', 'private')
            public = openssl_call('pkey', '-in', 'private', '-pubout', '-outform', 'DER')[-32:].hex()
            (root / 'message').write_bytes(signed_message('sample', revision))
            signature = openssl_call('pkeyutl', '-sign', '-inkey', 'private', '-rawin', '-in', 'message').hex()
            runtime = SimpleNamespace(store=ConversationStore(root / 'chat.db'))
            records = CapabilityStore(runtime.store._connect)
            context = ExecutionContext('workspace', root, lambda path: src,
                                       permissions=frozenset({'workspace.write'}))
            transport = PackageManagement(runtime, context)
            arguments = dict(identity='sample', action='install', path='sample', signature=signature)
            with patch('core.config._PROJECT_ROOT', root):
                with self.assertRaises(PermissionError):
                    await transport.call_tool('capability_package', arguments, 'session')
                grant = dict(public_key=public, permissions=[], actions=['install','upgrade'])
                records.save('workspace', 'sample', grant, 'host_package_grant')
                with self.assertRaises(PermissionError):
                    await transport.call_tool('capability_package', {**arguments,'signature':'00'*64}, 'session')
                self.assertFalse(records.list('__host__','plugin_package'))
                result = await transport.call_tool('capability_package', arguments, 'session')
                self.assertEqual(result['deployment'], 'PENDING_RESTART')
                self.assertEqual(result['target'], revision)
                self.assertIsNone(records.list('__host__','plugin_package')['sample']['active'])
                metadata['permissions'] = ['scripts.execute']
                manifest.write_text(json.dumps(metadata))
                with self.assertRaises(PermissionError):
                    await transport.call_tool('capability_package', {**arguments,'action':'upgrade'}, 'session')
                self.assertEqual(records.list('__host__','plugin_package')['sample']['target'], revision)
