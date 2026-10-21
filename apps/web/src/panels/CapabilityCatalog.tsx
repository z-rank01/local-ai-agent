import {useEffect, useState} from 'react';
import {fetchCapabilities, setCapability, prepareCapability, type CapabilitySummary} from '../api';
import {formatBackendError} from '../lib/format';

/** Manifest inventory is deliberately distinct from live service health. */
export function CapabilityCatalog(): JSX.Element {
  const [rows, setRows] = useState<CapabilitySummary[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function refresh() {
    setLoading(true);
    try {setRows((await fetchCapabilities()).capabilities); setError('');}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setLoading(false);}
  }
  async function toggle(row: CapabilitySummary) {
    setLoading(true);
    try {await setCapability(row.id, row.registration !== 'registered'); await refresh();}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setLoading(false);}
  }
  async function prepare(row: CapabilitySummary) {
    setLoading(true);
    try {await prepareCapability(row.id); await refresh();}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setLoading(false);}
  }
  useEffect(() => {
    let active = true;
    void fetchCapabilities().then(result => {if (active) setRows(result.capabilities);})
      .catch(cause => {if (active) setError(formatBackendError(cause));});
    return () => {active = false;};
  }, []);
  useEffect(() => {
    if (!rows.some(row => row.environment?.state === 'PREPARING')) return;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [rows]);
  return <details>
    <summary>能力清单与工具登记</summary>
    <p className="panel-hint">可选执行环境须先准备，再启用；准备环境不会运行文档任务。</p>
    <button type="button" className="btn" disabled={loading} onClick={() => void refresh()}>
      {loading ? '刷新中…' : '刷新登记状态'}
    </button>
    {error ? <p role="alert" className="panel-hint">{error}</p> : null}
    {rows.map(row => <div key={row.id} className="field">
      <span className="field-label">{row.title ?? row.id}{row.version ? ` · ${row.version}` : ''}</span>
      <span className="panel-hint">{row.error ?? row.description}</span>
      <span className="panel-hint">{row.registration === 'invalid' ? '清单无效' :
        row.registration === 'registered' ? `已登记 ${row.tools.length} 个工具` : '工具未登记'}</span>
      {row.optional?.length ? <span className="panel-hint">可选依赖：{row.optional.join('、')}</span> : null}
      {row.environment_required ? <>
        <span className="panel-hint">环境：{({NOT_PREPARED:'尚未准备', PREPARING:'准备中，可继续使用聊天', PREPARED:'已准备', PREPARE_FAILED:'准备失败', INTERRUPTED:'准备中断'} as Record<string,string>)[row.environment?.state ?? 'NOT_PREPARED'] ?? row.environment?.state}
          {row.environment?.error ? ` · ${row.environment.error}` : ''}</span>
        <button className="btn" disabled={loading || row.environment?.state === 'PREPARING' || row.registration === 'registered'} onClick={() => void prepare(row)}>准备执行环境</button>
      </> : null}
      {row.management === 'managed' ? <button className="btn" disabled={loading} onClick={() => void toggle(row)}>
        {row.registration === 'registered' ? '停用插件' : '启用插件'}</button> : null}
      {Object.entries(row.lifecycle ?? {}).map(([id, state]) => <span className="panel-hint" key={id}>
        {({ACTIVE:'已激活', INACTIVE:'已停用', STARTING:'启动中', START_FAILED:'启动失败', DRAINING:'等待收尾', STOP_FAILED:'收尾未确认'} as Record<string, string>)[state.state] ?? state.state}
        {' · '}在途调用 {state.inflight}{state.error ? ` · ${state.error}` : ''}
      </span>)}
    </div>)}
  </details>;
}
