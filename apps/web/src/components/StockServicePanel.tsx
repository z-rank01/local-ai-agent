import {useEffect, useRef, useState} from 'react';
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
  const mutating = useRef(false);
  const revision = useRef(0);
  const [pendingAction, setPendingAction] = useState('');
  const [error, setError] = useState('');
  const [challenge, setChallenge] = useState('');
  const [query, setQuery] = useState('');
  const [reply, setReply] = useState('');
  useEffect(() => {
    let active = true;
    let first = true;
    const refresh = async () => {
      if (mutating.current) return;
      const requestRevision = ++revision.current;
      try {
        const value = await serviceRequest();
        if (!active || requestRevision !== revision.current) return;
        setStatus(value);
        if (first) {
          setRoot(value.settings.root); setStateDir(value.settings.state_dir); setPort(String(value.settings.port)); first = false;
        }
      } catch (e) { if (active && requestRevision === revision.current) setError(String(e)); }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    if (mutating.current) return;
    mutating.current = true;
    ++revision.current;
    setPendingAction(action);
    setBusy(true); setError('');
    try {
      const result = await serviceRequest({action, ...extra});
      if (result.challenge) setChallenge(result.challenge);
      else if (result.reply) setReply(result.reply);
      else { setStatus(result); setChallenge(''); }
      onChanged?.();
      if (action === 'operator') setQuery('');
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally {mutating.current = false; setBusy(false); setPendingAction('');}
  };
  const steps = status?.recovery?.steps ?? [];
  const online = !!status?.online;
  const completed = steps.filter(s => s.status === 'COMPLETED').length;
  const problems = status?.recovery?.problems ?? [];
  const humanTasks = status?.recovery?.human_tasks ?? [];
  const recoveryOpen = !!challenge || problems.length > 0;
  return <section className="stock-service-panel">
    <h2>股票技能</h2>
    <div className="skill-row">
      <div className="skill-row-main">
        <span className="skill-name">股票后台</span>
        <span className={online ? 'skill-badge on' : 'skill-badge'}>{online ? '已开启' : '已关闭'}</span>
      </div>
      <button type="button" className={online ? 'switch-button on' : 'switch-button'}
        disabled={busy} onClick={() => void act(online ? 'stop' : 'start')}>
        {busy ? '切换中…' : online ? '关闭' : '开启'}
      </button>
    </div>
    {online && status?.runtime ? <div className="stock-summary">
      <span>任务执行 <b>{status.worker?.draining ? '正在收尾' : status.worker?.enabled ? '已开启' : '已暂停'}</b></span>
      <span>业务模式 <b>{label(status.runtime.mode)}</b></span>
      <span>已核对至 <b>{status.runtime.last_completed_day ?? '尚无记录'}</b></span>
    </div> : null}
    {!online && status?.message ? <p className="status-hint">{status.message}</p> : null}
    {!online && !status?.settings.root ? <p className="status-hint">首次使用请在下方“高级管理 → 连接配置”保存股票仓库路径。</p> : null}

    {online ? <details className="panel-details" open={recoveryOpen}>
      <summary>恢复与待办<span className="details-meta">{completed}/{steps.length} 已核对 · {label(status?.recovery?.batch?.status ?? '无恢复批次')}</span></summary>
      <div className="panel-details-body">
        <p className="status-hint">历史数据标为事后取得，离线期间未实时监控；恢复不自动重发模型请求。</p>
        {problems.map(p => <p key={p} role="status" className="status-hint">{p}</p>)}
        {humanTasks.map(t => <p key={t.id} className="status-hint">{t.body.message ?? t.kind}</p>)}
        <div className="status-actions">
          <button type="button" className="ghost-button tiny" disabled={busy || status?.worker?.busy} onClick={() => void act('recover')}>补跑 / 重试恢复</button>
          <button type="button" className="ghost-button tiny" disabled={busy || status?.worker?.busy || !!problems.length} onClick={() => void act('prepare_resume')}>核对并恢复运行</button>
        </div>
        {challenge ? <div role="alert"><p className="status-hint">确认已核对以上恢复结果与待办？恢复后允许正常研究；新订单仍需逐笔确认。</p>
          <div className="status-actions">
            <button type="button" className="ghost-button tiny" disabled={busy} onClick={() => void act('confirm_resume', {challenge})}>确认恢复</button>
            <button type="button" className="ghost-button tiny" onClick={() => setChallenge('')}>取消</button>
          </div>
        </div> : null}
      </div>
    </details> : null}

    <details className="panel-details">
      <summary>高级管理</summary>
      <div className="panel-details-body">
        <details className="panel-details nested">
          <summary>连接配置<span className="details-meta">{status?.settings.root ? `${status.settings.state_dir} · ${status.settings.port}` : '首次使用'}</span></summary>
          <div className="panel-details-body">
            <label>股票仓库路径<input value={root} onChange={e => setRoot(e.target.value)} placeholder="D:\Stock_Agent_Workspace" /></label>
            <label>状态目录<input value={stateDir} onChange={e => setStateDir(e.target.value)} /></label>
            <label>端口<input type="number" value={port} onChange={e => setPort(e.target.value)} /></label>
            <p className="status-hint">日常目录为 simulation；验收请使用独立目录，避免切错账户。修改前请先关闭股票技能。</p>
            <div className="status-actions">
              <button type="button" className="ghost-button tiny" disabled={busy || online} onClick={() => void act('configure', {root, state_dir: stateDir, port: Number(port)})}>保存配置</button>
            </div>
          </div>
        </details>
        {online ? <div className="status-actions">
          <button type="button" className="ghost-button tiny" disabled={busy || status?.worker?.enabled} onClick={() => void act('worker_start')}>开启任务执行</button>
          <button type="button" className="ghost-button tiny" disabled={busy || !status?.worker?.enabled} onClick={() => void act('worker_pause')}>暂停任务执行</button>
        </div> : null}
        {online ? <details className="panel-details nested">
          <summary>逐日恢复记录</summary>
          <div className="panel-details-body">
            {steps.map(s => <p key={s.id} className="status-hint">{s.body.day} · {label(s.status)}{s.body.error ? `：${s.body.error}` : ''}</p>)}
          </div>
        </details> : null}
        {online ? <details className="panel-details nested">
          <summary>人工操作<span className="details-meta">直接提交，不经过模型</span></summary>
          <div className="panel-details-body">
            <p className="status-hint">可输入“帮助”“账户”“权益待办”“恢复运行”“初始化 100000”。需要确认的操作会返回一次性确认指令；复制到此处提交。</p>
            <textarea aria-label="人工操作内容" value={query} onChange={e => setQuery(e.target.value)} />
            <div className="status-actions">
              <button type="button" className="ghost-button tiny" disabled={busy || !query.trim()} onClick={() => void act('operator', {query, request_id: crypto.randomUUID()})}>提交</button>
            </div>
            {reply && <pre className="stock-operator-reply">{reply}</pre>}
          </div>
        </details> : null}
      </div>
    </details>
    {error && <p role="alert" className="error-banner">{error}</p>}
  </section>;
}
