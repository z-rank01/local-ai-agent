import {useEffect, useRef, useState} from 'react';
import {fetchSkills, setWebsearch} from './api';
import {notify} from '../../lib/notify';
import {CapabilityCard, type SkillCard} from '../../extensions/CapabilityCard';
import type {CapabilityPanelProps} from '../../extensions/contracts';

export default function SearchSettings({onChanged}: CapabilityPanelProps) {
  const [enabled, setEnabled] = useState(false);
  const [searchActive, setSearchActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mutating = useRef(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (mutating.current) return;
      try {
        const status = await fetchSkills();
        if (active && !mutating.current) {setEnabled(status.websearch.enabled); setSearchActive(status.websearch.active);}
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const reportError = (cause: unknown) => {
    const message = cause instanceof Error ? cause.message : String(cause);
    setError(message);
    notify(message, 'error');
  };

  const toggleWebsearch = async (next: boolean) => {
    if (mutating.current) {
      return;
    }
    mutating.current = true;
    setBusy(true);
    setError('');
    try {
      const status = await setWebsearch(next);
      setEnabled(status.websearch.enabled);
      setSearchActive(status.websearch.active);
      onChanged?.();
    } catch (cause) {
      reportError(cause);
    } finally {
      mutating.current = false;
      setBusy(false);
    }
  };

  const card: SkillCard = {
      id: 'websearch',
      icon: 'i-wifi',
      name: '联网搜索',
      desc: '联网检索并读取网页正文',
      badge: !enabled ? '已关闭' : searchActive ? '已开启' : '启动中…',
      badgeClass: !enabled ? 'chip chip-quiet' : searchActive ? 'chip chip-ok' : 'chip chip-warn',
      checked: enabled,
      busy,
      onToggle: toggleWebsearch,
      body: (
        <p className="panel-hint">开启后模型可联网检索与读取网页正文；关闭后只使用本地文件与历史会话。</p>
      ),
    };
  return <>{error && <p className="run-error" role="alert">{error}</p>}<CapabilityCard card={card}/></>;
}
