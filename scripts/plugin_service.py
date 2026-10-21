"""Resolve registered IDs for the launcher; never execute a supplied command."""
import argparse
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import config
from core.conversation_store import ConversationStore
from core.workspaces import WorkspaceManager
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_services import service_name, service_spec
from scripts.workspace_compose import generate


def resolve(plugin_id, service_id, workspace_id, operation):
    if operation not in {'prepare', 'start', 'stop', 'probe'}:
        raise ValueError('Unknown fixed plugin service operation')
    spec = service_spec(ROOT, plugin_id, service_id)
    name = service_name(plugin_id, service_id, workspace_id)
    if operation == 'probe':
        return {'name': name, 'image': spec['image'], 'revision': spec['revision']}
    manager = WorkspaceManager(ConversationStore(), config.WORKSPACE_PATH)
    manager.get(workspace_id)  # Must already be an installed host workspace.
    store = CapabilityStore(manager.store._connect)
    identity = plugin_id + ':' + service_id
    binding = store.list(workspace_id, 'plugin_service').get(identity)
    if binding is None:
        if operation == 'stop':
            raise ValueError('Plugin service has no saved binding')
        used = {9510, 9511}
        for record in manager.records():
            used.update((record['files_port'], record['runner_port']))
            used.update(row['port'] for row in store.list(record['id'], 'plugin_service').values())
        while True:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            if port not in used:
                break
        binding = {'plugin_id': plugin_id, 'service_id': service_id, 'port': port}
        store.save(workspace_id, identity, binding, 'plugin_service')
    generate(workspace_id)
    return {'name': name, 'image': spec['image'], 'revision': spec['revision'], 'port': binding['port']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'start', 'stop', 'probe'])
    parser.add_argument('plugin_id')
    parser.add_argument('service_id')
    parser.add_argument('workspace_id')
    args = parser.parse_args()
    print(json.dumps(resolve(args.plugin_id, args.service_id, args.workspace_id, args.operation)))
