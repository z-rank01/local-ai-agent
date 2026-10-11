"""Fixed public replay through the running unique-entry BFF; no server or account setup."""
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json,sys,time,hashlib,importlib.util
chat_root=Path(__file__).resolve().parents[1]
policy_path=chat_root/'scripts/quality_replay_policy.py'
spec=importlib.util.spec_from_file_location('frozen_replay_policy',policy_path)
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
sys.path.insert(0,'D:/Stock_Agent_Workspace')
from services.paper.common import digest,dumps,stamp,RuleError
from services.paper.store import Store
from services.paper.data import atomic_json
from services.agents.direct_research import invoke
from services.agents.registry import Registry
from services.agents.adapters.specialist import material_packet

class ReplayPause(BaseException):
    pass
from services.agents.contracts import Request,Scope,validate_output
from services.paper.company_research import run_company,canonical_output,isolate_invalid_analysis
import httpx

root=chat_root/'data/private/quality-first-20261010/comparison-v5'
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
state=Path('D:/Stock_Agent_Workspace/simulation/in45-method-acceptance-20261010')
store=Store(state/'state.sqlite');registry=Registry();snapshot=manifest['execution']
assert snapshot['bundle_hash']==registry.manifest()['bundle_hash'],'frozen code differs; do not mix versions'
assert manifest['policy_sha256']==hashlib.sha256(policy_path.read_bytes()).hexdigest(),'replay policy changed after freezing'
assert manifest['driver_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'replay driver changed after freezing'
assert digest(json.loads((root/'rubric.json').read_text(encoding='utf-8')))==manifest['rubric_hash']
headers={'X-Harness-Session':(chat_root/'data/private/web-session.token').read_text().strip(),
         'Origin':'http://127.0.0.1:9510'}
def preflight():
    if (root/'PAUSE').exists():raise ReplayPause('acceptance paused at step boundary')
    try:
        response=httpx.get('http://127.0.0.1:9510/api/admin/stock-service',headers=headers,timeout=15,trust_env=False)
        response.raise_for_status()
        status=response.json()
        ready=(status['settings']['state_dir']=='simulation/in45-method-acceptance-20261010'
               and status['online'] and status['worker'].get('research_only') is True
               and status['research_model']['model']==snapshot['model'])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ReplayPause('acceptance environment unavailable; checkpoints retained: '+type(exc).__name__) from exc
    if not ready:raise ReplayPause('acceptance environment changed; checkpoints retained')
preflight()

class ReplayRunner:
    def __init__(self,sample,case):self.sample,self.case=sample,case;self.calls=[]
    def check_cancelled(self,job_id):
        preflight()
    def run(self,duty,material,**kw):
        rid=kw['request_id']; path=root/('node-'+digest(rid)+'.json')
        if path.exists():
            output=json.loads(path.read_text(encoding='utf-8'))['output']
            return self.receipt(rid,output)
        evidence_ids=kw['evidence_ids']
        profile='standard';feedback=None;failures=0;attempt=0
        while True:
            preflight()
            assert registry.manifest()['bundle_hash']==snapshot['bundle_hash'], 'source changed; new calls stopped'
            attempt+=1
            call_id=digest([manifest['batch'],rid,attempt])
            old=store.one('SELECT status,body FROM model_calls WHERE id=?',(call_id,))
            if old:
                saved=json.loads(old['body'])
                decision=policy.restore_attempt(old['status'],saved,duty,failures)
                if decision['action']=='expand':
                    profile='expanded';continue
                if decision['action']=='repair':
                    failures+=1;feedback=decision['feedback'];continue
                output=decision['output']
                atomic_json(path,{'output':output});return self.receipt(rid,output)
            scope=Scope(account_id='paper',mode='paper',strategy_version=store.account()['version'],
                policy_hash=registry.manifest()['policy_hash'],research_id=rid,subject_id=kw.get('subject_id','public-replay'),purpose='new_research')
            request=Request(request_id=rid,duty_id=duty,duty_version=registry.spec(duty).get('version',registry.data['defaults']['duty_version']),
                bundle_hash=snapshot['bundle_hash'],model_call_budget=6,scope=scope,as_of=self.case['as_of'],
                evidence_ids=evidence_ids,material=material)
            packet,_=material_packet(request)
            if feedback:packet['validation_feedback']=feedback
            meta={'batch':manifest['batch'],'sample':self.sample,'request_id':rid,'input_hash':digest(packet),
                  'execution_hash':snapshot['execution_hash'],'profile':profile,'started_at':stamp()}
            with store.tx() as db:
                db.execute('INSERT INTO model_calls VALUES(?,?,?,?,?)',(call_id,stamp()[:10],'QUALITY_FIXED_'+duty,'RUNNING',dumps(meta)))
            started=time.monotonic();reply={};status='UNKNOWN';output=None;response_received=False
            try:
                reply=invoke(snapshot,duty,packet,state,profile)
                response_received=True
                if reply.get('unfinished'):raise RuntimeError('UNKNOWN')
                if reply.get('known_failure'):
                    status='FAILED';raise RuleError(reply.get('provider_error') or '明确失败')
                if reply.get('finish_reason')=='length':
                    status='TRUNCATED'
                else:
                    parsed=json.loads(reply['raw'].strip().removeprefix('```json').removesuffix('```').strip())
                    if duty=='C03':parsed=canonical_output(parsed,material,evidence_ids)
                    scope=Scope(account_id='paper',mode='paper',strategy_version=store.account()['version'],
                        policy_hash=registry.manifest()['policy_hash'],research_id=rid,subject_id=kw.get('subject_id','public-replay'),purpose='new_research')
                    request=Request(request_id=rid,duty_id=duty,duty_version=registry.spec(duty).get('version',registry.data['defaults']['duty_version']),
                        bundle_hash=snapshot['bundle_hash'],model_call_budget=6,scope=scope,as_of=self.case['as_of'],
                        evidence_ids=evidence_ids,material=material)
                    output=policy.validate_result(registry,request,parsed,arm=self.sample['arm'],final_repair=failures>=2)
                    status='SUCCEEDED'
            except (RuleError,ValueError,TypeError,KeyError) as exc:
                if response_received and not reply.get('unfinished'):
                    status='FAILED';feedback=str(exc);failures+=1
                else:
                    status='UNKNOWN';feedback='未知结果：'+type(exc).__name__
            except Exception as exc:
                feedback='未知结果：'+type(exc).__name__
            elapsed=round(time.monotonic()-started,2)
            meta.update(reply=reply,elapsed_seconds=elapsed,status=status,validation_error=feedback)
            if output:meta['validated_output']=output
            with store.tx() as db:db.execute('UPDATE model_calls SET status=?,body=? WHERE id=?',(status,dumps(meta),call_id))
            self.calls.append({'id':call_id,'status':status,'seconds':elapsed,'usage':reply.get('usage',{})})
            print(self.sample['case_id'],self.sample['arm'],self.sample['repetition'],duty,status,elapsed,flush=True)
            if output:break
            if status=='TRUNCATED' and duty=='C03' and profile=='standard':profile='expanded';continue
            if status=='FAILED' and not reply.get('known_failure') and failures<3:continue
            output={'run_status':'UNKNOWN' if status=='UNKNOWN' else 'FAILED','data_quality':'PARTIAL',
                'verdict':None,'report':feedback or status,'evidence_refs':[],'missing':[feedback or status],'data':({'truncated':True,'finish_reason':'length'} if status=='TRUNCATED' else {})}
            break
        atomic_json(path,{'output':output,'calls':self.calls})
        return self.receipt(rid,output)
    @staticmethod
    def receipt(rid,output):
        return SimpleNamespace(output=SimpleNamespace(model_dump=lambda **kw:deepcopy(output)),request=SimpleNamespace(request_id=rid))

def execute(sample):
    outpath=root/('result-'+sample['blind_id']+'.json')
    if outpath.exists():return
    if sample.get('blocked_by_unknown'):
        atomic_json(outpath,{'sample':sample,'output':{'run_status':'UNKNOWN','data_quality':'PARTIAL',
            'missing':['原样本调用结果未知，修正包没有换 ID 重发'],'data':{}},'calls':[],
            'blocked_by_unknown':sample['blocked_by_unknown'],'finished_at':stamp()})
        print('BLOCKED',sample['blind_id'],flush=True);return
    case=json.loads((root/(sample['case_id']+'.json')).read_text(encoding='utf-8'))
    assert digest(case['material'])==case['input_hash']
    runner=ReplayRunner(sample,case);started=time.monotonic()
    rid='quality-fixed:'+sample['blind_id']
    try:
        preflight()
        output=policy.execute_fixed_case(runner,case,sample,snapshot,root)
    except ReplayPause:
        print('PAUSED',sample['blind_id'],flush=True);return
    except Exception as exc:
        output={'run_status':'FAILED','data_quality':'PARTIAL','missing':[str(exc)],'data':{}}
    atomic_json(outpath,{'sample':sample,'input_hash':case['input_hash'],'output':output,
        'calls':runner.calls,'elapsed_seconds':round(time.monotonic()-started,2),'finished_at':stamp()})
    print('SAMPLE',sample['blind_id'],output.get('run_status'),flush=True)

if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        selected = manifest['samples']
        if '--long-first' in sys.argv:
            selected = [row for row in selected if row['case_id'] in ('Q05', 'Q06') and row['arm']=='quality' and row['repetition']==1]
        list(pool.map(execute,selected))
    results=[json.loads(p.read_text(encoding='utf-8')) for p in root.glob('result-*.json')]
    atomic_json(root/'execution-summary.json',{'total':len(results),'completed':sum(r['output'].get('run_status')=='COMPLETED' for r in results),
        'unknown':sum(r['output'].get('run_status')=='UNKNOWN' for r in results),'finished_at':stamp()})
    print('FINISHED',len(results),flush=True)
