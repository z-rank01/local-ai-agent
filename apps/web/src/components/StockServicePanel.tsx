import {useEffect, useState} from 'react';
import {DEFAULT_BASE_URL} from '../api';

type Step = {id: string; status: string; body: {day?: string; error?: string; attempts?: number}};
type ServiceStatus = {
  online: boolean; message?: string;
  settings: {root: string; state_dir: string; port: number; enabled: boolean; worker: boolean};
  worker?: {enabled: boolean; draining: boolean; busy: boolean};
  runtime?: {mode: string; reason: string; last_completed_day: string | null};
  recovery?: {batch: {status: string; from_day: string; to_day: string} | null; steps: Step[]; problems: string[];
    human_tasks: {id: string; kind: string; body: {message?: string}}[]};
};

const labels: Record<string, string> = {UNINITIALIZED: '未初始化', ACTIVE: '正常运行', AWAY_READONLY: '离开 · 只读', RECOVERING: '恢复核对中', NEEDS_REVIEW: '需要核查', PENDING: '等待执行', RUNNING: '执行中', COMPLETED: '已完成', BLOCKED: '有阻塞'};
const label = (s: string) => labels[s] ?? s;

async function serviceRequest(body?: Record<string, unknown>) {
  const res = await fetch(`${DEFAULT_BASE_URL}/api/admin/stock-service`, body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  } : undefined);
  const value = await res.json();
  if (!res.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '服务操作失败');
  return value;
}

export function StockServicePanel({onChanged}: {onChanged?: () => void}) {
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [root, setRoot] = useState('');
  const [stateDir, setStateDir] = useState('simulation');
  const [port, setPort] = useState('8765');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [challenge, setChallenge] = useState('');
  const [query, setQuery] = useState('');
  const [reply, setReply] = useState('');
  useEffect(() => {
    let active = true;
    let first = true;
    const refresh = async () => {
      try {
        const value = await serviceRequest();
        if (!active) return;
        setStatus(value);
        if (first) {
          setRoot(value.settings.root); setStateDir(value.settings.state_dir); setPort(String(value.settings.port)); first = false;
        }
      } catch (e) { if (active) setError(String(e)); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    setBusy(true); setError('');
    try {
      const result = await serviceRequest({action, ...extra});
      if (result.challenge) setChallenge(result.challenge);
      else if (result.reply) setReply(result.reply);
      else { setStatus(result); setChallenge(''); }
      onChanged?.();
      if (action === 'operator') setQuery('');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally {setBusy(false);}
  };
  const steps = status?.recovery?.steps ?? [];
  return <section className="stock-service-panel">
    <h2>股票服务</h2>
    <p>{status?.online ? '后台已连接' : '后台未运行 · 聊天可继续使用'}</p>
    <p>任务执行：{status?.worker?.draining ? '正在收尾' : status?.worker?.enabled ? '已开启' : '已暂停'}</p>
    {status?.runtime && <><p>业务模式：{label(status.runtime.mode)}</p><p>{status.runtime.reason}</p><p>已核对至：{status.runtime.last_completed_day ?? '尚无记录'}</p></>}
    <details><summary>连接配置（首次设置）</summary>
      <label>股票仓库路径<input value={root} onChange={e => setRoot(e.target.value)} placeholder="D:\Stock_Agent_Workspace" /></label>
      <label>状态目录<input value={stateDir} onChange={e => setStateDir(e.target.value)} /></label>
      <label>端口<input type="number" value={port} onChange={e => setPort(e.target.value)} /></label>
      <p>日常目录为 simulation；验收请使用独立目录，避免切错账户。</p>
      <button disabled={busy || status?.online} onClick={() => void act('configure', {root, state_dir: stateDir, port: Number(port)})}>保存配置</button>
    </details>
    <div className="status-actions">
      <button disabled={busy || status?.online} onClick={() => void act('start')}>开启股票后台</button>
      <button disabled={busy || !status?.online || status?.worker?.enabled} onClick={() => void act('worker_start')}>开启任务执行</button>
      <button disabled={busy || !status?.online || !status?.worker?.enabled} onClick={() => void act('worker_pause')}>暂停任务执行</button>
      <button disabled={busy || !status?.online} onClick={() => void act('stop')}>关闭股票后台</button>
    </div>
    {status?.message && !status.online && <p>{status.message}</p>}
    {status?.online && <>
      <h3>恢复与待办</h3>
      <p>{steps.filter(s => s.status === 'COMPLETED').length} / {steps.length} 个交易日已核对 · {label(status.recovery?.batch?.status ?? '无恢复批次')}</p>
      <p>历史数据标为事后取得，离线期间未实时监控。恢复不自动重发模型请求。</p>
      {status.recovery?.problems.map(p => <p key={p} role="status">{p}</p>)}
      <div className="status-actions">
        <button disabled={busy || status.worker?.busy} onClick={() => void act('recover')}>补跑 / 重试恢复</button>
        <button disabled={busy || status.worker?.busy || !!status.recovery?.problems.length} onClick={() => void act('prepare_resume')}>核对并恢复运行</button>
      </div>
      {challenge && <div role="alert"><p>确认已核对以上恢复结果与待办？恢复后允许正常研究；新订单仍需逐笔确认。</p><button disabled={busy} onClick={() => void act('confirm_resume', {challenge})}>确认恢复</button><button onClick={() => setChallenge('')}>取消</button></div>}
      <details><summary>逐日恢复记录</summary>{steps.map(s => <p key={s.id}>{s.body.day} · {label(s.status)}{s.body.error ? `：${s.body.error}` : ''}</p>)}</details>
      {status.recovery?.human_tasks.map(t => <p key={t.id}>{t.body.message ?? t.kind}</p>)}
      <details><summary>人工操作（直接提交，不经过模型）</summary>
        <p>可输入“帮助”“账户”“权益待办”“恢复运行”“初始化 100000”。需要确认的操作会返回一次性确认指令；复制到此处提交。</p>
        <textarea aria-label="人工操作内容" value={query} onChange={e => setQuery(e.target.value)} />
        <button disabled={busy || !query.trim()} onClick={() => void act('operator', {query, request_id: crypto.randomUUID()})}>提交人工操作</button>
        {reply && <pre className="stock-operator-reply">{reply}</pre>}
      </details>
    </>}
    {error && <p role="alert" className="error-banner">{error}</p>}
    {busy && <p role="status">正在执行，请稍候…</p>}
  </section>;
}
