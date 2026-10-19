import tempfile
import unittest
from importlib import util
from pathlib import Path

import yaml

_MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "policy_engine.py"
_SPEC = util.spec_from_file_location("policy_engine", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_POLICY_ENGINE = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_POLICY_ENGINE)
PolicyEngine = _POLICY_ENGINE.PolicyEngine


class PolicyEnginePathTests(unittest.TestCase):
    def _engine(self) -> PolicyEngine:
        cfg = {
            "paths": {
                "allowed_prefixes": ["/workspace/"],
                "denied_prefixes": ["/workspace/.git", "/etc"],
                "write_only_prefixes": ["/workspace/skills/"],
            },
            "operations": {
                "allow_delete": True,
                "allow_shell_exec": True,
            },
            "files": {
                "max_size_bytes": 1024,
                "denied_extensions": [],
            },
            "execution": {},
        }
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        policy_path = Path(temp_dir.name) / "policy.yaml"
        policy_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return PolicyEngine(policy_path)

    def test_workspace_posix_paths_are_allowed(self) -> None:
        engine = self._engine()

        engine.check("file_list", {"directory": "/workspace/data/"})

    def test_workspace_backslash_paths_are_allowed(self) -> None:
        engine = self._engine()

        engine.check("file_read", {"path": r"\workspace\data\report.md"})

    def test_denied_prefix_still_blocks(self) -> None:
        engine = self._engine()

        with self.assertRaises(PermissionError):
            engine.check("file_read", {"path": "/workspace/.git/config"})

    def test_write_only_prefix_still_blocks_delete(self) -> None:
        engine = self._engine()

        with self.assertRaises(PermissionError):
            engine.check("file_delete", {"path": "/workspace/skills/example.py"})


class PolicyEngineShellTests(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine(_MODULE_PATH.parents[1] / 'config/policy.yaml')

    def test_command_suffix_in_python_identifier_is_not_a_shell_command(self):
        self.engine.check('shell_exec', {
            'command': "python -c \"for enc in ['utf-8']: print(enc)\""})

    def test_denied_commands_still_block_at_command_boundaries(self):
        for command in ('nc example.org 80', 'echo x; nc example.org 80',
                        '/usr/bin/nc example.org 80', 'rm /workspace/example'):
            with self.subTest(command=command), self.assertRaises(PermissionError):
                self.engine.check('shell_exec', {'command': command})

    def test_wrong_execution_parameter_reports_the_correct_tool_without_rewriting(self):
        params = {'command': 'pwd'}
        with self.assertRaisesRegex(PermissionError, 'code_exec.code.*shell_exec'):
            self.engine.check('code_exec', params)
        self.assertEqual(params, {'command': 'pwd'})
        with self.assertRaisesRegex(PermissionError, 'shell_exec.command.*code_exec'):
            self.engine.check('shell_exec', {'code':'print(1)'})
        self.engine.check('code_exec', {'code':'print(1)'})
        self.engine.check('shell_exec', {'command':'pwd'})

    def test_regex_compilation_is_not_dynamic_python_compilation(self):
        for code in ("import json,re\npattern=re.compile(r'\\d+')",
                     "import re as regex\npattern=regex.compile(r'\\d+')",
                     "message='compile( is a function name'"):
            with self.subTest(code=code):
                self.engine.check('code_exec', {'code':code})
        for code in ("compile('print(1)', 'x', 'exec')",
                     "compile ('print(1)', 'x', 'exec')",
                     "import builtins\nbuiltins.compile('1', 'x', 'eval')",
                     "import re, builtins\nre=builtins\nre.compile('1', 'x', 'eval')",
                     "import re\ndef f(re): return re.compile('1', 'x', 'eval')",
                     "import re\nre.compile=other\nre.compile('1')",
                     "import re\nfrom other import re\nre.compile('1')"):
            with self.subTest(code=code), self.assertRaisesRegex(PermissionError, 'compile'):
                self.engine.check('code_exec', {'code':code})


if __name__ == "__main__":
    unittest.main()
