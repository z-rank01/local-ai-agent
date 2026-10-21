import {useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {
  fetchResearchMethods,
  fetchSkills,
  fetchStockService,
  setWebsearch,
  stockServiceAction,
  updateResearchMethod,
} from '../api';
import type {ResearchMethod} from '../api';
import type {ConversationSummary} from '../types';
import {askConfirm} from '../lib/confirm';
import {notify} from '../lib/notify';
import {Collapse} from '../ui/Collapse';
import {Dropdown} from '../ui/Dropdown';
import {Icon} from '../ui/Icon';
import {Switch} from '../ui/Switch';
import {StockPanel} from './StockPanel';
import {CapabilityCatalog} from './CapabilityCatalog';

export type SkillsPanelProps = {
  conversation?: ConversationSummary;
  onChanged?: () => void;
  onMethodChanged?: (conversation: ConversationSummary) => void;
};

type SkillCard = {
  id: string;
  icon: string;
  name: string;
  desc: string;
  badge: string;
  badgeClass: string;
  checked: boolean;
  busy: boolean;
  onToggle: (next: boolean) => void;
  body: ReactNode;
};

export function SkillsPanel({
  conversation,
  onChanged,
  onMethodChanged,
}: SkillsPanelProps): JSX.Element {
  const [enabled, setEnabled] = useState(false);
  const [searchActive, setSearchActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stockOnline, setStockOnline] = useState(false);
  const [stockBusy, setStockBusy] = useState(false);
  const [error, setError] = useState('');
  const [methods, setMethods] = useState<ResearchMethod[]>([]);
  const [methodBusy, setMethodBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [openCards, setOpenCards] = useState<Record<string, boolean>>({});
  /* 变更进行中时跳过轮询，避免 5 秒一次的读回把开关状态覆盖回去 */
  const mutating = useRef(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (mutating.current) {
        return;
      }
      try {
        const status = await fetchSkills();
        if (active) {
          setEnabled(status.websearch.enabled);
          setSearchActive(status.websearch.active);
        }
      } catch {
        // 后端未就绪：保留上一次状态，下一轮重试
      }
      try {
        const response = await fetchResearchMethods();
        if (active) {
          setMethods(response.methods);
        }
      } catch {
        if (active) {
          setMethods([]);
        }
      }
      try {
        const status = await fetchStockService();
        if (active) {
          setStockOnline(status.online);
        }
      } catch {
        // 同上：控制接口未就绪时保留上一次状态
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
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
      onChanged?.();
    } catch (cause) {
      reportError(cause);
    } finally {
      mutating.current = false;
      setBusy(false);
    }
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

  const cards: SkillCard[] = [
    {
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
    },
    {
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
    },
  ];

  const keyword = filter.trim().toLowerCase();
  const visible = cards.filter(
    (card) => !keyword || `${card.name}${card.desc}`.toLowerCase().includes(keyword),
  );

  return (
    <>
      <div className="skill-filter">
        <Icon name="i-search" className="ico-sm" />
        <input
          className="input input-sm"
          type="search"
          autoComplete="off"
          placeholder="搜索技能…"
          aria-label="搜索技能"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>
      {error ? (
        <div className="panel-ok" role="alert">
          <Icon name="i-alert" className="ico-sm" />
          <span>{error}</span>
        </div>
      ) : null}
      <div className="skill-list">
        {visible.map((card) => {
          const open = Boolean(openCards[card.id]);
          return (
            <div key={card.id} className="skill" data-open={open ? 'true' : 'false'}>
              <div className="skill-top">
                <button
                  type="button"
                  className="skill-open"
                  aria-expanded={open}
                  aria-label={`${open ? '收起' : '展开'}${card.name}设置`}
                  onClick={() => setOpenCards((current) => ({...current, [card.id]: !open}))}
                >
                  <span className="skill-ico">
                    <Icon name={card.icon} />
                  </span>
                  <span className="skill-main">
                    <span className="skill-name">{card.name}</span>
                    <span className="skill-desc">{card.desc}</span>
                  </span>
                  <Icon name="i-chev-down" className="ico-sm skill-chev" />
                </button>
                <span className={card.badgeClass}>{card.badge}</span>
                <Switch
                  checked={card.checked}
                  disabled={card.busy}
                  onChange={(next) => void card.onToggle(next)}
                  label={`启用${card.name}`}
                />
              </div>
              <Collapse open={open} className="skill-body">
                <div className="skill-content">{card.body}</div>
              </Collapse>
            </div>
          );
        })}
        {visible.length === 0 ? <p className="ws-empty">没有匹配的技能</p> : null}
      </div>
      <p className="panel-hint">开关即时生效；点卡片就地展开设置。</p>
      <CapabilityCatalog />
    </>
  );
}
