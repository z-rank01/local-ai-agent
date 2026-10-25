"""Internal cold-start deployment step, invoked only by start-daily.ps1."""
import json
from pathlib import Path
import sys
import socket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.conversation_store import ConversationStore
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_packages import PluginPackages
from harness.infrastructure.plugin_deployment import apply_pending, confirm_build


if __name__ == '__main__':
    packages = PluginPackages(CapabilityStore(ConversationStore()._connect),
                              ROOT / 'data/private/plugin-packages', ROOT / 'plugins')
    if sys.argv[1:] == ['confirm-build']:
        confirm_build(packages)
    elif not sys.argv[1:]:
        states = packages.store.list('__host__', 'plugin_package')
        if any(row.get('deployment') in ('PENDING_RESTART', 'BUILD_REQUIRED') for row in states.values()):
            for port in (9510, 9511):
                with socket.socket() as probe:
                    probe.settimeout(1)
                    if probe.connect_ex(('127.0.0.1', port)) == 0:
                        raise SystemExit('Pending plugin deployment requires normal full stack exit first.')
        result = apply_pending(packages)
        print(json.dumps(result, ensure_ascii=True))
        raise SystemExit(1 if result['errors'] else 0)
    else:
        raise SystemExit('Unsupported internal deployment operation')
