import {useEffect, useRef, useState} from 'react';
import {fetchResearchMethods, fetchStockService, stockServiceAction, updateResearchMethod, type ResearchMethod} from './api';
import {askConfirm} from '../../lib/confirm';
import {notify} from '../../lib/notify';
import {Dropdown} from '../../ui/Dropdown';
import {CapabilityCard, type SkillCard} from '../../extensions/CapabilityCard';
import type {CapabilityPanelProps} from '../../extensions/contracts';
import {StockPanel} from './StockPanel';

export default function FinanceSettings({conversation, onChanged, onMethodChanged}: CapabilityPanelProps) {
  const [stockOnline, setStockOnline] = useState(false);
  const [stockBusy, setStockBusy] = useState(false);
  const [methods, setMethods] = useState<ResearchMethod[]>([]);
  const [methodBusy, setMethodBusy] = useState(false);
  const [error, setError] = useState('');
  const mutating = useRef(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (mutating.current) return;
      try {
        const [status, catalog] = await Promise.all([fetchStockService(), fetchResearchMethods()]);
        if (active && !mutating.current) {setStockOnline(status.online); setMethods(catalog.methods);}
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
    setError(message); notify(message, 'error');
  };
  const toggleStock = async (next: boolean) => {
    if (mutating.current) {
      return;
    }
    if (!next) {
      const confirmed = await askConfirm({
        title: '关闭股票后台',
        body: '关闭将暂停后台任务执行与简报生成，进行中的研究会在安全边界收尾。',
        okLabel: '关闭',
        danger: true,
      });
      if (!confirmed) {
        return;
      }
    }
    mutating.current = true;
    setStockBusy(true);
    setError('');
    try {
      const result = await stockServiceAction({action: next ? 'start' : 'stop'});
      setStockOnline(Boolean(result.online));
      onChanged?.();
    } catch (cause) {
      reportError(cause);
    } finally {
      mutating.current = false;
      setStockBusy(false);
    }
  };

  const selectMethod = async (methodId: string) => {
    if (!conversation || methodBusy) {
      return;
    }
    setMethodBusy(true);
    setError('');
    try {
      onMethodChanged?.(await updateResearchMethod(conversation.id, methodId));
    } catch (cause) {
      reportError(cause);
    } finally {
      setMethodBusy(false);
    }
  };

  const card: SkillCard = {
      id: 'stock',
      icon: 'i-chart',
      name: '股票后台',
      desc: '后台任务执行与每日简报',
      badge: stockBusy ? (stockOnline ? '关闭中…' : '启动中…') : stockOnline ? '已开启' : '已关闭',
      badgeClass: stockBusy ? 'chip chip-warn' : stockOnline ? 'chip chip-ok' : 'chip chip-quiet',
      checked: stockOnline,
      busy: stockBusy,
      onToggle: toggleStock,
      body: (
        <>
          <div className="field">
            <span className="field-label">本会话研究方法</span>
            <Dropdown
              value={conversation?.research_method ?? 'auto'}
              options={[
                {value: 'auto', label: '自动编排'},
                ...methods.map((method) => ({value: method.id, label: method.title})),
              ]}
              disabled={!conversation || methodBusy || methods.length === 0}
              ariaLabel="本会话研究方法"
              onChange={(methodId) => void selectMethod(methodId)}
            />
          </div>
          <p className="panel-hint">只影响本会话后续解释和明确提交的新研究任务；不适用的职责使用自动方法。</p>
          <StockPanel conversation={conversation} onChanged={onChanged} />
        </>
      ),
    };
  return <>{error && <p className="run-error" role="alert">{error}</p>}<CapabilityCard card={card}/></>;
}
