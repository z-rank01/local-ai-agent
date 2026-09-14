import {useEffect, useRef, useState} from 'react';
import {fetchSkills, setWebsearch} from '../api';

export function SkillsPanel({onChanged}: {onChanged?: () => void}) {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
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

  return <section className="skills-panel">
    <h2>技能</h2>
    <div className="skill-row">
      <span>联网搜索</span>
      <button type="button" className="ghost-button tiny" disabled={busy} onClick={() => void toggle()}>
        {busy ? '切换中…' : enabled ? '已开启 · 点击关闭' : '已关闭 · 点击开启'}
      </button>
    </div>
    <p className="status-hint">开启后可让模型联网检索与读取网页；首次开启需要启动搜索容器，可能要等几秒。</p>
    {error && <p role="alert" className="error-banner">{error}</p>}
  </section>;
}
