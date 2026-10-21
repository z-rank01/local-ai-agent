import {useEffect, useState} from 'react';
import {fetchCapabilities, type CapabilitySummary} from '../api';
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
  useEffect(() => {
    let active = true;
    void fetchCapabilities().then(result => {if (active) setRows(result.capabilities);})
      .catch(cause => {if (active) setError(formatBackendError(cause));});
    return () => {active = false;};
  }, []);
  return <details>
    <summary>能力清单与工具登记</summary>
    <p className="panel-hint">清单描述可扩展能力；工具登记不等于服务健康。当前启停仍使用上方开关。</p>
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
    </div>)}
  </details>;
}
