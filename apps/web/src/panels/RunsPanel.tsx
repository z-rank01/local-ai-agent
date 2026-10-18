import {useState} from 'react';
import type {RunList, RunSummary} from '../types';
import {formatBackendError} from '../lib/format';
import {RUN_LIVE,runLabel} from '../lib/runs';

function time(value: string) { return new Date(value).toLocaleString('zh-CN',{hour12:false}); }
function elapsed(run: RunSummary) {
  const end=RUN_LIVE.has(run.status)?Date.now():new Date(run.updated_at).getTime();
  const minutes=Math.max(0,Math.floor((end-new Date(run.created_at).getTime())/60000));
  return minutes<60?`${minutes} 分钟`:`${Math.floor(minutes/60)} 小时 ${minutes%60} 分钟`;
}

export function RunControls({run,onControl}:{run:RunSummary;onControl:(id:string,action:'pause'|'resume'|'stop')=>Promise<void>}) {
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const act=async(action:'pause'|'resume'|'stop') => {
    setPending(true);setError('');
    try { await onControl(run.id,action); } catch(cause) { setError(formatBackendError(cause)); }
    finally { setPending(false); }
  };
  const canPause=run.status==='RUNNING'||run.status==='QUEUED';
  const canStop=RUN_LIVE.has(run.status)||run.status==='QUEUED'||(run.source==='stock'&&run.status==='INTERRUPTED');
  const canResume=run.status==='PAUSED'||(!RUN_LIVE.has(run.status)&&run.resumable);
  return <>
    <div className="run-actions">
      {canPause&&<button className="btn btn-quiet btn-sm" disabled={pending} onClick={()=>void act('pause')}>暂停</button>}
      {canResume&&<button className="btn btn-quiet btn-sm" disabled={pending||!run.resumable} title={run.resume_blocker} onClick={()=>void act('resume')}>继续</button>}
      {canStop&&<button className="btn btn-quiet btn-sm" disabled={pending||run.status==='STOPPING'} onClick={()=>void act('stop')}>停止</button>}
      {pending&&<span role="status">正在提交操作…</span>}
    </div>
    {error&&<p className="run-error" role="alert">{error}</p>}
    {!run.resumable&&['PAUSED','INTERRUPTED','STOPPED','QUEUED'].includes(run.status)&&<p className="run-note">{run.resume_blocker||'没有可继续的安全检查点'}</p>}
  </>;
}

export function RunsPanel({data,error,onOpen,onControl}:{data:RunList;error:string;onOpen:(id:string)=>void;
  onControl:(id:string,action:'pause'|'resume'|'stop')=>Promise<void>}) {
  const live=data.runs.filter(r=>RUN_LIVE.has(r.status)||r.status==='QUEUED');
  const recent=data.runs.filter(r=>!live.includes(r));
  const stock=data.stock_service;
  return <section className="runs-panel" aria-label="全部工作区任务">
    <p className="run-note">切换会话只切换观察对象。暂停会等待当前步骤结束；停止会保留已有成果。</p>
    <p className="run-note">股票服务：{stock.online?'在线':'离线'} · 领取任务：{stock.worker?.enabled?'开启':'暂停'} · 实际执行：{stock.worker?.busy?'有任务':'空闲'}</p>
    {stock.message&&<p className="run-error">{stock.message}</p>}
    {error&&<p className="run-error" role="alert">任务状态暂不可确认：{error}</p>}
    {data.shutdown_error&&<p className="run-error" role="alert">整栈退出未完成：{data.shutdown_error}。服务保留，请处理后重试退出。</p>}
    {[['当前任务',live],['近期结束',recent]] .map(([title,rows])=><div key={String(title)}>
      <h3>{String(title)}</h3>
      {(rows as RunSummary[]).length===0&&<p className="run-note">暂无记录</p>}
      {(rows as RunSummary[]).map(run=><article key={run.id} className="run-row">
        <div className="run-heading"><strong>{run.title}</strong><span className={'run-status '+run.status.toLowerCase()}>{runLabel(run.status)}</span></div>
        <p>{run.phase||'正在准备'}{run.error?' · '+run.error:''}</p>
        <p className="run-note">{run.source==='stock'?'股票专业任务':run.workspace_id} · {run.model||'模型待确定'}</p>
        <p className="run-note">耗时 {elapsed(run)} · 开始 {time(run.created_at)} · 最近活动 {time(run.updated_at)}</p>
        {run.business_status&&<p className="run-note">研究状态：{run.business_status}</p>}
        {run.publication_status&&<p className="run-note">HTML：{run.publication_status==='PUBLISHED'?'已发布，可进入会话阅读':'尚未发布'}；执行结束不代表研究完整。</p>}
        {run.conversation_id&&<button className="btn btn-quiet btn-sm" onClick={()=>onOpen(run.conversation_id!)}>进入会话</button>}
        <RunControls run={run} onControl={onControl}/>
      </article>)}
    </div>)}
  </section>;
}
