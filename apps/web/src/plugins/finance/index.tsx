import {useEffect, useState} from 'react';
import {fetchResearchMethods, updateResearchMethod, type ResearchMethod} from './api';
import {Dropdown} from '../../ui/Dropdown';
import {formatBackendError} from '../../lib/format';
import type {CapabilityPanelProps} from '../../extensions/contracts';
import {StockPanel} from './StockPanel';

export default function FinanceSettings({conversation, onChanged, onMethodChanged}: CapabilityPanelProps) {
  const [methods, setMethods] = useState<ResearchMethod[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void fetchResearchMethods().then(value => {if (active) setMethods(value.methods);})
      .catch(cause => {if (active) setError(formatBackendError(cause));});
    return () => {active = false;};
  }, []);
  async function selectMethod(id: string) {
    if (!conversation) return;
    setBusy(true); setError('');
    try {onMethodChanged?.(await updateResearchMethod(conversation.id, id));}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  return <>
    {error && <p className="run-error" role="alert">{error}</p>}
    <div className="field"><span className="field-label">本会话研究方法</span>
      <Dropdown value={conversation?.research_method ?? 'auto'}
        options={[{value: 'auto', label: '自动编排'}, ...methods.map(method => ({value: method.id, label: method.title}))]}
        disabled={!conversation || busy || !methods.length} ariaLabel="本会话研究方法"
        onChange={id => void selectMethod(id)}/>
    </div>
    <p className="panel-hint">方法只影响本会话后续新研究。插件开关不等于开启日常任务，账户确认仍由你操作。</p>
    <StockPanel conversation={conversation} onChanged={onChanged}/>
  </>;
}
