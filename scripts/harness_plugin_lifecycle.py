"""Queue and verify one trusted package stage; the original launcher owns all starts."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.conversation_store import ConversationStore
from core.run_store import RunStore
from core.ui_session import session_token
from core.workspaces import WorkspaceManager, tool_token
from harness.infrastructure.capability_store import CapabilityStore
from scripts.measure_harness_performance import stop_stack

BASE = 'http://127.0.0.1:9510'
OUT = ROOT / 'data/private/harness-plugin-lifecycle'
IDENTITY = 'file-integrity'
ORIGINAL = '58ea78e58db52244bcd9443222e4ead883fb7aa5da72da8749ca5b72b49e10e6'
UPDATE = 'f3b099f30aadac13c539017a9077efdc35db570eeac6a13790495a5257a365d3'
FAILURE = 'b46916651bff0a4bc235455c89d25ba936f75cafa500c19e8641b711181fe9dd'
STAGES = {
    'update': ('02-update', 'activate', UPDATE, '1.0.1', 'sha256', ORIGINAL),
    'rollback': ('03-rollback', 'rollback', ORIGINAL, '1.0.0', None, UPDATE),
    'failure': ('04-failed-build', 'activate', FAILURE, '1.0.0', None, ORIGINAL),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=tuple(STAGES))
    parser.add_argument('operation', choices=('queue', 'verify'))
    args = parser.parse_args()
    stage, action, revision, version, algorithm, before = STAGES[args.stage]
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {'X-Harness-Session': session_token(), 'Origin': BASE}
    with httpx.Client(base_url=BASE, headers=headers, timeout=180, trust_env=False) as client:
        state = CapabilityStore(ConversationStore()._connect).list('__host__', 'plugin_package')[IDENTITY]
        if args.operation == 'queue':
            active = [row['id'] for row in RunStore(ConversationStore()).list(limit=100)
                      if row['status'] in ('RUNNING', 'PAUSING', 'PAUSED', 'STOPPING')]
            if active:
                raise RuntimeError('仍有运行，禁止完整退出部署：' + ','.join(active))
            if state.get('active', {}).get('revision') != before:
                raise RuntimeError('当前版本不是本阶段已核验的起点')
            response = client.post('/api/plugin-packages/' + IDENTITY,
                                   json={'action': action, 'revision': revision, 'trusted': True})
            response.raise_for_status()
            queued = response.json()
            if queued.get('deployment') != 'PENDING_RESTART':
                raise RuntimeError('候选未进入原冷启动队列')
            (OUT / (stage + '-queued.json')).write_text(json.dumps(queued, ensure_ascii=False, indent=2), encoding='utf-8')
            stop_stack()
            print(json.dumps({'stage': stage, 'queued': revision}, ensure_ascii=False))
            return
        response = client.post('/api/capabilities/' + IDENTITY, json={'enabled': True})
        response.raise_for_status()
        packages = client.get('/api/plugin-packages').json()['packages']
        package = next(row for row in packages if row['id'] == IDENTITY)
        capabilities = client.get('/api/capabilities').json()['capabilities']
        capability = next(row for row in capabilities if row['id'] == IDENTITY)
        if capability['version'] != version or package.get('active', {}).get('revision') != (
                ORIGINAL if args.stage == 'failure' else revision):
            raise RuntimeError('冷启动后的版本不符合预期：' + stage)
        activation = client.post('/api/workspaces/e6ec6edca523/activate')
        activation.raise_for_status()
        workspace = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get('e6ec6edca523')
        source = ROOT / 'data/workspace/.harness/e6ec6edca523/cases/meeting-notes/m1-v8/record.md'
        response = client.post(f'http://127.0.0.1:{workspace["files_port"]}/tool/file_fingerprint',
            headers={'X-Workspace-Token': tool_token(workspace['id'])},
            json={'path': '/workspace/cases/meeting-notes/m1-v8/record.md',
                  'expected_sha256': sha256(source.read_bytes()).hexdigest()})
        response.raise_for_status()
        tool = response.json()
        if tool.get('algorithm') != algorithm or not tool.get('matches_expected'):
            raise RuntimeError('实际工具结果不符合部署版本：' + stage)
        row = {'stage': stage, 'action': action, 'requested_revision': revision,
               'package': package, 'capability': capability, 'tool': tool}
        if args.stage == 'failure':
            failed_log = ROOT / 'data/logs/web-build.log.plugin-failed'
            if not failed_log.is_file() or package.get('deployment') != 'BUILD_FAILED':
                raise RuntimeError('故障候选没有产生预期的真实构建失败和恢复记录')
            row['failed_build_log'] = str(OUT / 'web-build.plugin-failed.log')
            shutil.copyfile(failed_log, row['failed_build_log'])
            if not package.get('pending') or package['pending']['revision'] != FAILURE:
                raise RuntimeError('故障候选未保留待审版本')
        (OUT / (stage + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'stage': stage, 'version': capability['version'],
                          'deployment': package.get('deployment'), 'algorithm': tool.get('algorithm')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
