"""Offline integration checks against compose.in1.yml; only creates IN1 test files."""
import asyncio
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT/'data/in1/workspace'

async def main():
    results = []
    async with httpx.AsyncClient(timeout=20,trust_env=False) as client:
        async def post(port,tool,params):
            response = await client.post(f'http://127.0.0.1:{port}/tool/{tool}',json=params)
            return response
        async def stream(tool,params):
            response = await post(19102,tool+'/stream',params)
            response.raise_for_status()
            packets = [json.loads(line) for line in response.text.splitlines()]
            return packets,packets[-1]['result']
        async def check(name,condition):
            assert condition,name
            results.append(name)
        response = await post(19101,'file_write',{'path':'/workspace/in1-edit.txt','content':'中文第一行\n数量：3\n最后一行\n'})
        response.raise_for_status()
        read = (await post(19101,'file_read',{'path':'/workspace/in1-edit.txt'})).json()
        edit = await post(19101,'file_edit',{'path':'/workspace/in1-edit.txt','old_text':'数量：3','new_text':'数量：4','expected_sha256':read['sha256']})
        await check('edit Chinese diff',edit.status_code==200 and '+数量：4' in edit.json()['diff'])
        conflict = await post(19101,'file_edit',{'path':'/workspace/in1-edit.txt','old_text':'数量：4','new_text':'数量：5','expected_sha256':read['sha256']})
        await check('stale hash rejected',conflict.status_code==409)
        read2 = (await post(19101,'file_read',{'path':'/workspace/in1-edit.txt'})).json()
        await check('edit readback unchanged surrounding text',read2['content']==read['content'].replace('数量：3','数量：4'))
        ambiguous = await post(19101,'file_edit',{'path':'/workspace/in1-edit.txt','old_text':'行','new_text':'字','expected_sha256':read2['sha256']})
        await check('ambiguous match rejected',ambiguous.status_code==409)
        second = await post(19101,'file_edit',{'path':'/workspace/in1-edit.txt','old_text':'数量：4','new_text':'数量：5','expected_sha256':read2['sha256']})
        await check('second precise edit',second.status_code==200)
        denied = await post(19101,'file_read',{'path':'/etc/passwd'})
        await check('file root guard',denied.status_code==403)
        packets,result = await stream('shell_exec',{'command':"printf 'first\\n'; sleep 1; printf 'second\\n'; printf 'problem\\n' >&2; exit 7"})
        await check('stdout stderr and nonzero exit',result['exit_code']==7 and 'second' in result['stdout'] and 'problem' in result['stderr'])
        await check('incremental shell progress',sum(p['event']=='output' for p in packets)>=2)
        packets,result = await stream('shell_exec',{'command':"sleep 10 & wait",'timeout':1})
        await check('timeout kills group',result['timed_out'] and result['exit_code']!=0)
        packets,result = await stream('code_exec',{'code':"print('x'*200000)"})
        await check('bounded output',result['truncated'] and len(result['stdout'])==51200)
        packets,result = await stream('code_exec',{'code':"print('before'); raise ValueError('intentional')"})
        await check('python partial failure',result['exit_code']!=0 and 'before' in result['stdout'] and 'ValueError' in result['stderr'])
        skill = "SKILL_METADATA = {'description':'IN1 double', 'dependencies':[], 'parameters':{'type':'object','properties':{'n':{'type':'number'}},'required':['n']}}\ndef run(params):\n    return {'value':params['n']*2}\n"
        registered = (await post(19102,'skill_register',{'skill_name':'in1_double','code':skill,'auto_install_deps':False})).json()
        await check('register skill',registered.get('success'))
        listing = (await post(19102,'skill_list',{})).json()
        await check('discover skill','in1_double' in json.dumps(listing))
        packets,result = await stream('skill_run',{'skill_name':'in1_double','params':{'n':21}})
        await check('run reusable skill',result.get('result',{}).get('value')==42)
        packets,result = await stream('skill_run',{'skill_name':'in1_double','params':{'n':7}})
        await check('reuse skill with different params',result.get('result',{}).get('value')==14)
        large_skill = "SKILL_METADATA = {'description':'synthetic large local output', 'dependencies':[]}\ndef run(params):\n    return {'local_result':'SYNTHETIC_LOCAL_ONLY_'*10000}\n"
        response = await post(19102,'skill_register',{'skill_name':'in1_large_local','code':large_skill,'auto_install_deps':False})
        response.raise_for_status()
        packets,result = await stream('skill_run',{'skill_name':'in1_large_local','params':{}})
        await check('truncated local result not projected to model',result['truncated'] and 'SYNTHETIC_LOCAL_ONLY_' not in json.dumps(result.get('model_observation',{})) and bool(result.get('error')))
        # Stop only this explicitly created test subprocess; a delayed file would prove a surviving child.
        marker = 'in1-cancel-marker.txt'
        marker_path = WORKSPACE/marker
        if marker_path.exists(): marker_path.unlink()
        async with client.stream('POST','http://127.0.0.1:19102/tool/shell_exec/stream',json={'command':"(sleep 2; echo alive > /workspace/"+marker+") & wait",'timeout':10}) as response:
            async for line in response.aiter_lines():
                if json.loads(line).get('event')=='started': break
        await asyncio.sleep(3)
        await check('disconnect stops child process',not marker_path.exists())
        packets,result = await stream('shell_exec',{'command':"id -u; test ! -e /var/run/docker.sock; test ! -e /app/.env; touch /app/in1-must-fail"})
        await check('nonroot readonly and no host credentials mount',result['exit_code']!=0 and result['stdout'].strip()=='1001')
    output={'passed':len(results),'checks':results,'real_model_calls':0}
    (ROOT/'data/in1/tool-checks.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False))

asyncio.run(main())
