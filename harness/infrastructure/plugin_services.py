"""Fixed container service declarations; request payloads never contain commands."""
import json
import re
from pathlib import Path
from harness.contracts.package_revision import package_revision

IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{0,47}')


def service_spec(project: Path, plugin_id: str, service_id: str):
    if not IDENTIFIER.fullmatch(plugin_id) or not IDENTIFIER.fullmatch(service_id):
        raise ValueError('Invalid registered plugin/service ID')
    packages = (project / 'plugins').resolve()
    package = (packages / plugin_id).resolve()
    if not package.is_relative_to(packages):
        raise ValueError('Plugin service package escapes root')
    manifest = package / 'plugin.json'
    if not manifest.resolve().is_relative_to(package):
        raise ValueError('Plugin service manifest escapes package')
    metadata = json.loads(manifest.read_text(encoding='utf-8'))
    if metadata.get('harness_api') != 1 or metadata.get('id') != plugin_id:
        raise ValueError('Invalid plugin service API or identity')
    spec = metadata.get('services', {}).get(service_id)
    if not isinstance(spec, dict) or set(spec) - {'port', 'dockerfile', 'memory_mb', 'cpus'}:
        raise ValueError('Unknown service or unsupported service declaration')
    port, memory, cpus = spec.get('port'), spec.get('memory_mb', 1024), spec.get('cpus', 1)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid internal service port')
    if type(memory) is not int or not 128 <= memory <= 8192:
        raise ValueError('Invalid service memory limit')
    if type(cpus) not in (float, int) or not 0 < cpus <= 8:
        raise ValueError('Invalid service CPU limit')
    dockerfile = (package / spec['dockerfile']).resolve()
    if not dockerfile.is_relative_to(package) or not dockerfile.is_file():
        raise ValueError('Service build file must be inside its package')
    version = metadata.get('version', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', version):
        raise ValueError('Invalid container version')
    return {**spec, 'memory_mb': memory, 'cpus': cpus,
            'revision': package_revision(package),
            'dockerfile': dockerfile.relative_to(project.resolve()).as_posix(),
            'image': f'local-ai-agent-plugin-{plugin_id}-{service_id}:{version}'}


def service_name(plugin_id: str, service_id: str, workspace_id: str):
    if (not IDENTIFIER.fullmatch(plugin_id) or not IDENTIFIER.fullmatch(service_id) or
            (workspace_id != 'default' and not re.fullmatch('[a-f0-9]{12}', workspace_id))):
        raise ValueError('Invalid service identity')
    return f'plugin-{plugin_id}-{service_id}-{workspace_id}'


def compose_service(project, snapshot, plugin_id, service_id, port, token):
    """Pure topology construction used only by the unique launcher generator."""
    spec = service_spec(project, plugin_id, service_id)
    if type(port) is not int or not 1024 <= port <= 65535 or port in (9510, 9511):
        raise ValueError('Invalid saved service port')
    name = service_name(plugin_id, service_id, snapshot.id)
    network = name + '-net'
    mounts = [{'type': 'bind', 'source': str(snapshot.managed), 'target': '/workspace',
               'bind': {'create_host_path': False}},
              {'type': 'tmpfs', 'target': '/workspace/.harness', 'read_only': True, 'tmpfs': {'size': 1024}}]
    mounts.extend({'type': 'bind', 'source': str(host), 'target': virtual, 'bind': {'create_host_path': False}}
                  for virtual, host in snapshot.roots.items() if virtual != '/workspace')
    health = ('import os,urllib.request; urllib.request.urlopen(urllib.request.Request('
              f'"http://127.0.0.1:{spec["port"]}/health", '
              'headers={"X-Workspace-Token":os.environ["WORKSPACE_TOOL_TOKEN"]}),timeout=3).close()')
    service = {'container_name': name, 'image': spec['image'], 'profiles': ['plugin-services'],
        'build': {'context': str(project.resolve()), 'dockerfile': spec['dockerfile'],
                  'labels': {'local-ai-agent.revision': spec['revision']}},
        'ports': [f'127.0.0.1:{port}:{spec["port"]}'], 'networks': [network],
        'environment': {'WORKSPACE_TOOL_TOKEN': token, 'WORKSPACE_ID': snapshot.id,
            'WORKSPACE_VERSION': str(snapshot.version), 'WORKSPACE_ROOTS': json.dumps(list(snapshot.roots)),
            'WORKSPACE_ROOT': '/workspace'},
        'volumes': mounts, 'labels': {'local-ai-agent.workspace': snapshot.id,
            'local-ai-agent.plugin': plugin_id, 'local-ai-agent.service': service_id},
        'mem_limit': f'{spec["memory_mb"]}m', 'cpus': spec['cpus'],
        'security_opt': ['no-new-privileges:true'], 'cap_drop': ['ALL'],
        'healthcheck': {'test': ['CMD', 'python', '-c', health], 'interval': '5s',
            'timeout': '4s', 'retries': 12, 'start_period': '10s'}, 'stop_grace_period': '40s'}
    return name, service, {network: {'driver': 'bridge', 'internal': True}}
