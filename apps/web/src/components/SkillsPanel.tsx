import {useEffect, useRef, useState} from 'react';
import {fetchResearchMethods, fetchSkills, setWebsearch, updateResearchMethod} from '../api';
import type {ResearchMethod} from '../api';
import type {ConversationSummary} from '../types';

export function SkillsPanel({onChanged, conversation, onMethodChanged}: {
  onChanged?: () => void;
  conversation?: ConversationSummary;
  onMethodChanged?: (conversation: ConversationSummary) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [methods, setMethods] = useState<ResearchMethod[]>([]);
  const [methodBusy, setMethodBusy] = useState(false);
  const mutating = useRef(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (mutating.current) return;
      try {
        const status = await fetchSkills();
        if (active) setEnabled(status.websearch.enabled);
      } catch {
        // Backend not ready yet; the next interval retries.
      }
      try {
        const response = await fetchResearchMethods();
        if (active) setMethods(response.methods);
      } catch {
        if (active) setMethods([]);
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const toggle = async () => {
    if (mutating.current) return;
    mutating.current = true;
    setBusy(true);
    setError('');
    try {
      const status = await setWebsearch(!enabled);
      setEnabled(status.websearch.enabled);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  };

  const selectMethod = async (methodId: string) => {
    if (!conversation || methodBusy) return;
    setMethodBusy(true);
    setError('');
    try {
      onMethodChanged?.(await updateResearchMethod(conversation.id, methodId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMethodBusy(false);
    }
  };

  return <section className="skills-panel">
    <h2>技能</h2>
    <div className="skill-row">
      <div className="skill-row-main">
        <span className="skill-name">联网搜索</span>
        <span className={enabled ? 'skill-badge on' : 'skill-badge'}>{enabled ? '已开启' : '已关闭'}</span>
      </div>
      <button type="button" className={enabled ? 'switch-button on' : 'switch-button'}
        disabled={busy} onClick={() => void toggle()}>
        {busy ? '切换中…' : enabled ? '关闭' : '开启'}
      </button>
    </div>
    <p className="status-hint">开启后模型可联网检索与读取网页；首次启动搜索容器需等待几秒。</p>
    <label className="research-method-field">
      <span>本会话研究方法</span>
      <select value={conversation?.research_method ?? 'auto'}
        disabled={!conversation || methodBusy || methods.length === 0}
        onChange={(event) => void selectMethod(event.target.value)}>
        <option value="auto">自动编排</option>
        {methods.map((method) => <option key={method.id} value={method.id}>{method.title}</option>)}
      </select>
    </label>
    <p className="status-hint">只影响本会话后续解释和明确提交的新研究任务；不适用的职责使用自动方法。</p>
    {error && <p role="alert" className="error-banner">{error}</p>}
  </section>;
}
