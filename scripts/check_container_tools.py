"""Offline integration checks against the daily tool containers (docker-compose.yml).

Creates only synthetic container-check files under /workspace; no model calls.
Requires the daily stack to be running (scripts/start-daily.ps1).
"""
import asyncio
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / 'data' / 'workspace'
FILES_PORT = int(os.environ.get('SKILL_FILES_PORT', '9101'))
RUNNER_PORT = int(os.environ.get('SKILL_RUNNER_PORT', '9102'))


async def main():
    results = []

    async def post(port, tool, params):
        response = await client.post(f'http://127.0.0.1:{port}/tool/{tool}', json=params)
        return response

    async def stream(tool, params):
        response = await post(RUNNER_PORT, tool + '/stream', params)
        response.raise_for_status()
        packets = [json.loads(line) for line in response.text.splitlines()]
        return packets, packets[-1]['result']

    async def check(name, condition):
        assert condition, name
        results.append(name)

    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        response = await post(FILES_PORT, 'file_write', {'path': '/workspace/container-check-edit.txt', 'content': '中文第一行\n数量：3\n最后一行\n'})
        response.raise_for_status()
        read = (await post(FILES_PORT, 'file_read', {'path': '/workspace/container-check-edit.txt'})).json()
        edit = await post(FILES_PORT, 'file_edit', {'path': '/workspace/container-check-edit.txt', 'old_text': '数量：3', 'new_text': '数量：4', 'expected_sha256': read['sha256']})
        await check('edit Chinese diff', edit.status_code == 200 and '+数量：4' in edit.json()['diff'])
        conflict = await post(FILES_PORT, 'file_edit', {'path': '/workspace/container-check-edit.txt', 'old_text': '数量：4', 'new_text': '数量：5', 'expected_sha256': read['sha256']})
        await check('stale hash rejected', conflict.status_code == 409)
        read2 = (await post(FILES_PORT, 'file_read', {'path': '/workspace/container-check-edit.txt'})).json()
        await check('edit readback unchanged surrounding text', read2['content'] == read['content'].replace('数量：3', '数量：4'))
        ambiguous = await post(FILES_PORT, 'file_edit', {'path': '/workspace/container-check-edit.txt', 'old_text': '行', 'new_text': '字', 'expected_sha256': read2['sha256']})
        await check('ambiguous match rejected', ambiguous.status_code == 409)
        second = await post(FILES_PORT, 'file_edit', {'path': '/workspace/container-check-edit.txt', 'old_text': '数量：4', 'new_text': '数量：5', 'expected_sha256': read2['sha256']})
        await check('second precise edit', second.status_code == 200)
        denied = await post(FILES_PORT, 'file_read', {'path': '/etc/passwd'})
        await check('file root guard', denied.status_code == 403)
        packets, result = await stream('shell_exec', {'command': "printf 'first\\n'; sleep 1; printf 'second\\n'; printf 'problem\\n' >&2; exit 7"})
        await check('stdout stderr and nonzero exit', result['exit_code'] == 7 and 'second' in result['stdout'] and 'problem' in result['stderr'])
        await check('incremental shell progress', sum(p['event'] == 'output' for p in packets) >= 2)
        packets, result = await stream('shell_exec', {'command': "sleep 10 & wait", 'timeout': 1})
        await check('timeout kills group', result['timed_out'] and result['exit_code'] != 0)
        packets, result = await stream('code_exec', {'code': "print('x'*200000)"})
        await check('bounded output', result['truncated'] and len(result['stdout']) == 51200)
        packets, result = await stream('code_exec', {'code': "print('before'); raise ValueError('intentional')"})
        await check('python partial failure', result['exit_code'] != 0 and 'before' in result['stdout'] and 'ValueError' in result['stderr'])
        skill = "SKILL_METADATA = {'description':'container-check double', 'dependencies':[], 'parameters':{'type':'object','properties':{'n':{'type':'number'}},'required':['n']}}\ndef run(params):\n    return {'value':params['n']*2}\n"
        registered = (await post(RUNNER_PORT, 'skill_register', {'skill_name': 'container_check_double', 'code': skill, 'auto_install_deps': False})).json()
        await check('register skill', registered.get('success'))
        listing = (await post(RUNNER_PORT, 'skill_list', {})).json()
        await check('discover skill', 'container_check_double' in json.dumps(listing))
        packets, result = await stream('skill_run', {'skill_name': 'container_check_double', 'params': {'n': 21}})
        await check('run reusable skill', result.get('result', {}).get('value') == 42)
        packets, result = await stream('skill_run', {'skill_name': 'container_check_double', 'params': {'n': 7}})
        await check('reuse skill with different params', result.get('result', {}).get('value') == 14)
        large_skill = "SKILL_METADATA = {'description':'synthetic large local output', 'dependencies':[]}\ndef run(params):\n    return {'local_result':'SYNTHETIC_LOCAL_ONLY_'*10000}\n"
        response = await post(RUNNER_PORT, 'skill_register', {'skill_name': 'container_check_large_local', 'code': large_skill, 'auto_install_deps': False})
        response.raise_for_status()
        packets, result = await stream('skill_run', {'skill_name': 'container_check_large_local', 'params': {}})
        await check('truncated local result not projected to model', result['truncated'] and 'SYNTHETIC_LOCAL_ONLY_' not in json.dumps(result.get('model_observation', {})) and bool(result.get('error')))
        # Stop only this explicitly created test subprocess; a delayed file would prove a surviving child.
        marker = 'container-check-cancel-marker.txt'
        marker_path = WORKSPACE / marker
        if marker_path.exists():
            marker_path.unlink()
        async with client.stream('POST', f'http://127.0.0.1:{RUNNER_PORT}/tool/shell_exec/stream', json={'command': "(sleep 2; echo alive > /workspace/" + marker + ") & wait", 'timeout': 10}) as response:
            async for line in response.aiter_lines():
                if json.loads(line).get('event') == 'started':
                    break
        await asyncio.sleep(3)
        await check('disconnect stops child process', not marker_path.exists())
        packets, result = await stream('shell_exec', {'command': "id -u; test ! -e /var/run/docker.sock; test ! -e /app/.env; touch /app/container-check-must-fail"})
        await check('nonroot readonly and no host credentials mount', result['exit_code'] != 0 and result['stdout'].strip() == '1001')

        # ── IN4.16: Agent Skills (SKILL.md) packages ──────────────────────
        import shutil
        pkg_dir = WORKSPACE / 'skills' / 'container-check-pkg'
        bad_dir = WORKSPACE / 'skills' / 'container-check-bad'
        for stale in (pkg_dir, bad_dir):
            if stale.exists():
                shutil.rmtree(stale)
        (pkg_dir / 'references').mkdir(parents=True)
        (pkg_dir / 'scripts').mkdir(parents=True)
        (pkg_dir / 'SKILL.md').write_text(
            '---\nname: container-check-pkg\ndescription: 合成标准技能包，用于容器集成检查\n---\n\n# 指南正文\n\n用于验证渐进披露。\n',
            encoding='utf-8')
        (pkg_dir / 'references' / 'guide.md').write_text('指南内容', encoding='utf-8')
        (pkg_dir / 'scripts' / 'hello.py').write_text('print("hi")', encoding='utf-8')
        bad_dir.mkdir(parents=True)
        (bad_dir / 'SKILL.md').write_text('---\nname: container-check-bad\n---\nno description\n', encoding='utf-8')
        try:
            listing = (await post(RUNNER_PORT, 'skill_list', {})).json()
            skills = listing.get('skills', [])
            entry = next((s for s in skills if s.get('name') == 'container-check-pkg'), None)
            await check('SKILL.md package discovered', entry is not None and entry.get('kind') == 'skill_md')
            await check('package listing hides body (progressive disclosure)', entry is not None and 'body' not in entry and '指南正文' not in json.dumps(entry, ensure_ascii=False))
            bad = next((s for s in skills if s.get('name') == 'container-check-bad'), None)
            await check('invalid package reported with error', bad is not None and bad.get('valid') is False and 'description' in bad.get('error', ''))
            info = (await post(RUNNER_PORT, 'skill_info', {'skill_name': 'container-check-pkg'})).json()
            await check('package info exposes body and reference list', '指南正文' in info.get('body', '') and 'references/guide.md' in info.get('references', []))
            await check('package info carries injection-defense note', '参考' in info.get('security_note', ''))
            guided = (await post(RUNNER_PORT, 'skill_run', {'skill_name': 'container-check-pkg', 'params': {}})).json()
            await check('package run refused with actionable guidance', guided.get('kind') == 'skill_md' and 'skill_info' in guided.get('error', ''))
            removed = (await post(RUNNER_PORT, 'skill_unregister', {'skill_name': 'container-check-pkg'})).json()
            await check('package unregistered via skill_unregister', removed.get('success') is True and not pkg_dir.exists())
            listing2 = (await post(RUNNER_PORT, 'skill_list', {})).json()
            await check('package disappears after unregister', not any(s.get('name') == 'container-check-pkg' for s in listing2.get('skills', [])))
        finally:
            for stale in (pkg_dir, bad_dir):
                if stale.exists():
                    shutil.rmtree(stale)
    output = {'passed': len(results), 'checks': results, 'real_model_calls': 0}
    (ROOT / 'data' / 'tool-checks.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False))


asyncio.run(main())
