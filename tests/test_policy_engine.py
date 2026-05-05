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


if __name__ == "__main__":
    unittest.main()