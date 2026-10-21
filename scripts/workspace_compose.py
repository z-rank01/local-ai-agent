"""Generate a fixed Compose topology for the launcher (never invoke Docker here)."""
import copy
import json
import re
import sys
import logging
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

import yaml
from core import config
from core.conversation_store import ConversationStore
from core.workspaces import WorkspaceManager, WorkspaceSnapshot, tool_token
from harness.infrastructure.capability_store import CapabilityStore
from harness.infrastructure.plugin_services import compose_service


def generate(apply_workspace_id=None):
    manager = WorkspaceManager(ConversationStore(), config.WORKSPACE_PATH)
    capabilities = CapabilityStore(manager.store._connect)
    if apply_workspace_id is not None and apply_workspace_id != "default" and not re.fullmatch("[a-f0-9]{12}", apply_workspace_id):
        raise ValueError("Invalid workspace id")
    manager.manifest(apply_workspace_id)
    records = json.loads((root / 'data/private/workspace-containers.json').read_text(encoding='utf-8'))
    topology = yaml.safe_load((root / 'docker-compose.yml').read_text(encoding='utf-8'))
    files = copy.deepcopy(topology['services']['skill-files'])
    runner = copy.deepcopy(topology['services']['skill-runner'])
    for record in records:
        snapshot = WorkspaceSnapshot(record, config.WORKSPACE_PATH.resolve())
        for kind, template, port, inside in [('files', files, record['files_port'], 8100), ('runner', runner, record['runner_port'], 8200)]:
            if not isinstance(port, int) or not 1024 <= port <= 65535 or port in (9510, 9511):
                raise ValueError('Invalid tool port')
            service = copy.deepcopy(template)
            name = f'skill-{kind}' if record['id'] == 'default' else f"ws-{record['id']}-{kind}"
            service['container_name'] = name
            service['image'] = f'local-ai-agent-skill-{kind}:latest'
            service['ports'] = [f'127.0.0.1:{port}:{inside}']
            service['labels'] = {'local-ai-agent.workspace': record['id']}
            network = 'ws-' + record['id'] + '-net'
            topology['networks'][network] = {'driver': 'bridge'}
            service['networks'] = [network]
            service['environment'].update(WORKSPACE_TOOL_TOKEN=tool_token(record['id']), WORKSPACE_ID=record['id'], WORKSPACE_VERSION=str(record['version']),
                WORKSPACE_ROOTS=json.dumps(list(snapshot.roots)), WORKSPACE_CWD=snapshot.cwd)
            mounts = [{'type': 'bind', 'source': str(snapshot.managed), 'target': '/workspace', 'bind': {'create_host_path': False}},
                      {'type': 'tmpfs', 'target': '/workspace/.harness', 'read_only': True, 'tmpfs': {'size': 1024}}]
            for virtual, host in snapshot.roots.items():
                if virtual == '/workspace': continue
                if not host.is_absolute(): raise ValueError('Root must be absolute')
                mounts.append({'type': 'bind', 'source': str(host), 'target': virtual, 'bind': {'create_host_path': False}})
            if kind == 'files':
                trash = root / 'data/trash' if record['id'] == 'default' else root / 'data/trash/workspaces' / record['id']
                trash.mkdir(parents=True, exist_ok=True)
                logs = root / 'data/logs/workspaces' / record['id']
                logs.mkdir(parents=True, exist_ok=True)
                mounts += [f'{trash}:/trash', f'{logs}:/logs', f'{root / "config"}:/config:ro']
                service['environment']['AUTO_GIT_COMMIT'] = files['environment'].get('AUTO_GIT_COMMIT', 'true') if record['id'] == 'default' else 'false'
            else:
                package_volume = 'runner-packages' if record['id'] == 'default' else 'ws-' + record['id'] + '-packages'
                topology['volumes'][package_volume] = {'driver': 'local'}
                mounts.append(package_volume + ':/packages')
            service['volumes'] = mounts
            topology['services'][name] = service
        for identity, binding in capabilities.list(record['id'], 'plugin_service').items():
            try:
                if identity != binding['plugin_id'] + ':' + binding['service_id']:
                    raise ValueError('Plugin service binding identity mismatch')
                name, service, networks = compose_service(root, snapshot, binding['plugin_id'],
                    binding['service_id'], binding['port'], tool_token(record['id']))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logging.warning('Optional plugin service %s unavailable: %s', identity, exc)
                continue
            topology['services'][name] = service
            topology['networks'].update(networks)
    destination = root / 'data/private/workspace-compose.json'
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(topology, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)


if __name__ == '__main__':
    generate(sys.argv[1] if len(sys.argv) > 1 else None)
