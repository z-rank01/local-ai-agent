import {useEffect, useId, useMemo, useRef, useState} from 'react';
import type {SkillsPanelProps} from '../capabilityPanelTypes';
import {Icon} from '../../ui/Icon';
import {Popover} from '../../ui/Popover';
import {confirmCapabilityLeave} from '../../lib/capabilityLeave';
import {useCapabilityRegistry} from '../../lib/capabilityRegistry';
import {CapabilityImport} from './CapabilityImport';
import {CapabilityEntry} from './CapabilityEntry';
import {
  buildManagerItems,
  filterManagerItems,
  managerCounts,
  type ManagerKind,
} from './capabilityManagerModel';

const EMPTY_IDS: string[] = [];

const FILTERS: {kind: ManagerKind; label: string}[] = [
  {kind: 'all', label: '全部'},
  {kind: 'skill', label: '技能'},
  {kind: 'plugin', label: '插件'},
];

export function CapabilityManager({
  workspaceId,
  selectedCapabilityIds = EMPTY_IDS,
  onCapabilitySelected,
  ...props
}: SkillsPanelProps): JSX.Element {
  const data = useCapabilityRegistry();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ManagerKind>('all');
  const [history, setHistory] = useState(false);
  const [expanded, setExpanded] = useState('');
  const [menu, setMenu] = useState(false);
  const [importKind, setImportKind] = useState<'plugin' | 'skill'>();
  const importAnchor = useRef<HTMLButtonElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const listId = useId();
  const searchId = useId();

  useEffect(() => {
    setImportKind(undefined);
    setExpanded((previous) => (previous.startsWith('skill:') ? '' : previous));
  }, [workspaceId]);

  const items = useMemo(
    () => buildManagerItems(data.plugins, data.packages, data.skills, selectedCapabilityIds),
    [data.packages, data.plugins, data.skills, selectedCapabilityIds],
  );
  const counts = managerCounts(items, history);
  const visible = filterManagerItems(items, query, filter, history);
  const loading = data.loading && items.length === 0;
  const noBase = counts.all === 0;
  const noResults = !noBase && visible.length === 0;

  async function change(action: () => void): Promise<void> {
    if (await confirmCapabilityLeave()) action();
  }

  async function imported(id: string): Promise<void> {
    const kind = importKind;
    setImportKind(undefined);
    setQuery('');
    setFilter('all');
    setHistory(false);
    await data.refresh();
    if (!id) return;
    const key = `${kind}:${id}`;
    setExpanded(key);
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(`[data-capability-key="${key}"]`)
        ?.scrollIntoView({block: 'nearest'});
    });
  }

  function moveFilter(event: React.KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? FILTERS.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + FILTERS.length) % FILTERS.length;
    const kind = FILTERS[next].kind;
    void change(() => {
      setFilter(kind);
      window.requestAnimationFrame(() => tabs.current[next]?.focus());
    });
  }

  return (
    <div className="capm">
      <section className="capm-hero" aria-labelledby={`${searchId}-title`}>
        <div className="capm-hero-copy">
          <p className="capm-eyebrow">工作区 · 能力</p>
          <h2 id={`${searchId}-title`}>能力管理</h2>
          <p className="capm-subtitle">启用后，能力会出现在对话的能力栏中。</p>
        </div>
        <div className="capm-hero-actions">
          <button
            ref={importAnchor}
            type="button"
            className="btn btn-secondary capm-import"
            aria-expanded={menu}
            onClick={() => void change(() => setMenu(!menu))}
          >
            <Icon name="i-plus" className="ico-sm" />
            导入
          </button>
          <button
            type="button"
            className="capm-refresh"
            aria-label="刷新能力列表"
            title="刷新能力列表"
            disabled={data.loading}
            aria-busy={data.loading}
            onClick={() => void data.refresh()}
          >
            <Icon name="i-refresh" className={data.loading ? 'is-spinning' : undefined} />
          </button>
        </div>
      </section>
      <Popover anchor={importAnchor} open={menu} onClose={() => setMenu(false)}>
        <div className="cap-menu" role="menu" aria-label="选择导入类型">
          {(['skill', 'plugin'] as const).map((kind) => (
            <button
              key={kind}
              role="menuitem"
              onClick={() => void change(() => { setMenu(false); setImportKind(kind); })}
            >
              导入 {kind === 'skill' ? 'Skill' : 'Plugin'}
            </button>
          ))}
        </div>
      </Popover>

      <label className="capm-search">
        <Icon name="i-search" className="ico-sm" />
        <input
          type="search"
          value={query}
          aria-label="搜索能力"
          placeholder="搜索名称、用途或来源"
          autoComplete="off"
          onChange={(event) => {
            const value = event.target.value;
            void change(() => setQuery(value));
          }}
        />
      </label>

      <div className="capm-filter-row">
        <div className="capm-segmented" role="group" aria-label="按能力类型筛选">
          {FILTERS.map((item, index) => {
            const count = item.kind === 'all' ? counts.all
              : item.kind === 'skill' ? counts.skill : counts.plugin;
            return (
              <button
                key={item.kind}
                ref={(node) => { tabs.current[index] = node; }}
                type="button"
                className="capm-seg"
                aria-pressed={filter === item.kind}
                aria-controls={listId}
                onClick={() => void change(() => setFilter(item.kind))}
                onKeyDown={(event) => moveFilter(event, index)}
              >
                {item.label}{' '}<span className="capm-seg-count">{count}</span>
              </button>
            );
          })}
        </div>
        <p className="capm-enabled-summary">{counts.enabled} 项已启用</p>
      </div>

      {data.error && (
        <div className="capm-alert" role="alert">
          <Icon name="i-alert" className="ico-sm" />
          <span>{data.error}</span>
          <button type="button" className="capm-retry" onClick={() => void data.refresh()}>重试</button>
        </div>
      )}
      {data.invalid.map((row) => (
        <p className="capm-invalid" key={row.name} role="status">
          <Icon name="i-alert" className="ico-sm" />
          <span>{row.name}：{row.error}</span>
        </p>
      ))}
      {importKind && (
        <CapabilityImport
          workspaceId={workspaceId}
          kind={importKind}
          onDone={(id) => void imported(id)}
          onCancel={() => setImportKind(undefined)}
        />
      )}

      {loading ? (
        <div className="capm-skeleton" aria-label="正在读取能力" role="status">
          {[0, 1, 2].map((row) => (
            <div className="capm-skeleton-row" key={row}>
              <span className="capm-skeleton-icon" />
              <span className="capm-skeleton-copy"><i /><i /></span>
            </div>
          ))}
        </div>
      ) : noBase ? (
        <div className="capm-empty">
          <span className="capm-empty-mark"><Icon name="i-spark" className="ico-lg" /></span>
          <h3>还没有能力</h3>
          <p>导入一个能力，或刷新当前工作区。</p>
        </div>
      ) : noResults ? (
        <div className="capm-empty">
          <span className="capm-empty-mark"><Icon name="i-search" className="ico-lg" /></span>
          <h3>没有找到能力</h3>
          <p>试试其他关键词，或清除筛选。</p>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void change(() => { setQuery(''); setFilter('all'); })}
          >
            清除筛选
          </button>
        </div>
      ) : (
        <div className="capm-group" id={listId} role="list" aria-label="能力列表" aria-busy={data.loading}>
          {visible.map((item) => (
            <CapabilityEntry
              key={item.kind === 'skill' ? `${workspaceId}:${item.key}` : item.key}
              entry={item.entry}
              workspaceId={workspaceId}
              variant="v2"
              selected={item.selected}
              onSelectedChange={(selected) => onCapabilitySelected?.(item.key, selected)}
              open={expanded === item.key}
              onOpen={() => void change(() => setExpanded(expanded === item.key ? '' : item.key))}
              refresh={() => data.refresh()}
              {...props}
            />
          ))}
        </div>
      )}

      <footer className="capm-footer">
        <span>{counts.all} 项能力</span>
        <span><strong>{counts.enabled}</strong> 项已启用 · <strong>{counts.selected}</strong> 项对话中</span>
        <button
          type="button"
          className="capm-history"
          aria-pressed={history}
          onClick={() => void change(() => setHistory(!history))}
        >
          {history ? '隐藏已移除' : '显示已移除'}{counts.removed > 0 ? ` (${counts.removed})` : ''}
        </button>
      </footer>
      <p className="panel-hint capm-note">
        Plugin 为本机能力，Skill 属于当前工作区。启停等待服务确认；版本与安装变更按详情提示生效。
      </p>
    </div>
  );
}
