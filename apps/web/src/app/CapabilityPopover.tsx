import {useRef} from 'react';
import type {KeyboardEvent as ReactKeyboardEvent, RefObject} from 'react';
import {Icon} from '../ui/Icon';
import {
  kindLabel,
  type CapabilityFilter,
  type CapabilityItem,
  type ToggleCapability,
} from '../panels/capabilities/capabilityModel';

const FILTERS: {value: CapabilityFilter; label: string}[] = [
  {value: 'all', label: '全部'},
  {value: 'skill', label: '技能'},
  {value: 'plugin', label: '插件'},
];

export type CapabilityPopoverProps = {
  titleId: string;
  listId: string;
  query: string;
  filter: CapabilityFilter;
  loading: boolean;
  error?: string;
  library: CapabilityItem[];
  visible: CapabilityItem[];
  selectedIds: Set<string>;
  announcement: string;
  searchRef: RefObject<HTMLInputElement>;
  onQueryChange: (query: string) => void;
  onFilterChange: (filter: CapabilityFilter) => void;
  onToggle: ToggleCapability;
  onClose: () => void;
  onRetry?: () => void;
  onManage?: () => void;
};

export function CapabilityPopover({
  titleId,
  listId,
  query,
  filter,
  loading,
  error,
  library,
  visible,
  selectedIds,
  announcement,
  searchRef,
  onQueryChange,
  onFilterChange,
  onToggle,
  onClose,
  onRetry,
  onManage,
}: CapabilityPopoverProps): JSX.Element {
  const listRef = useRef<HTMLDivElement | null>(null);

  const toggle = (item: CapabilityItem) => {
    const selected = selectedIds.has(item.id);
    if (!selected && item.status !== 'ready') {
      return;
    }
    void Promise.resolve(onToggle(item, !selected)).catch(() => {});
  };

  const focusFirstRow = () => {
    listRef.current?.querySelector<HTMLElement>('[data-capbar-row]')?.focus({preventScroll: true});
  };

  const onSearchKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && !loading) {
      event.preventDefault();
      focusFirstRow();
    }
  };

  const onListKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
      return;
    }
    const rows = [...event.currentTarget.querySelectorAll<HTMLElement>('[data-capbar-row]')];
    const index = rows.indexOf(document.activeElement as HTMLElement);
    if (index < 0) {
      return;
    }
    event.preventDefault();
    if (event.key === 'ArrowDown') {
      rows[Math.min(rows.length - 1, index + 1)]?.focus({preventScroll: true});
    } else if (index > 0) {
      rows[index - 1].focus({preventScroll: true});
    } else {
      searchRef.current?.focus({preventScroll: true});
    }
  };

  const onTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, current: CapabilityFilter) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
      return;
    }
    event.preventDefault();
    const index = FILTERS.findIndex((row) => row.value === current);
    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const next = FILTERS[(index + offset + FILTERS.length) % FILTERS.length];
    onFilterChange(next.value);
    window.requestAnimationFrame(() => {
      document.getElementById(`${listId}-tab-${next.value}`)?.focus({preventScroll: true});
    });
  };

  const errorBanner = error ? (
    <div className="capbar-pop-error" role="alert">
      <div>
        <strong>能力加载失败</strong>
        <small>{error}</small>
      </div>
      {onRetry ? (
        <button type="button" className="btn btn-quiet btn-sm" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  ) : null;

  let body: JSX.Element;
  if (loading) {
    body = (
      <div className="capbar-skeleton">
        <p className="capbar-loading" role="status">
          <span className="dots" aria-hidden="true"><i /><i /><i /></span>{' '}
          正在读取能力…
        </p>
        <div className="capbar-skeleton-rows" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <div className="capbar-skeleton-row" key={index}>
              <span className="capbar-skeleton-icon" />
              <span className="capbar-skeleton-copy"><i /><i /></span>
            </div>
          ))}
        </div>
      </div>
    );
  } else if (!visible.length) {
    if (error) {
      body = <>{errorBanner}<div className="capbar-empty-state"><p>暂时无法读取能力</p></div></>;
    } else if (library.length === 0) {
      body = (
        <div className="capbar-empty-state">
          <p>还没有已启用的能力</p>
          {onManage ? (
            <button type="button" className="btn btn-sm" onClick={onManage}>
              前往工作台启用
            </button>
          ) : null}
        </div>
      );
    } else {
      body = (
        <div className="capbar-empty-state">
          <p>没有找到匹配的能力</p>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              onQueryChange('');
              onFilterChange('all');
              searchRef.current?.focus({preventScroll: true});
            }}
          >
            清除搜索
          </button>
        </div>
      );
    }
  } else {
    body = (
      <>
        {errorBanner}
        {visible.map((item) => {
          const selected = selectedIds.has(item.id);
          const actionable = selected || item.status === 'ready';
          return (
            <button
              key={item.id}
              type="button"
              className={selected ? 'capbar-row is-selected' : 'capbar-row'}
              data-capbar-row
              data-capability-id={item.id}
              aria-pressed={selected}
              aria-disabled={actionable ? undefined : true}
              title={`${item.name} · ${item.status === 'ready' ? '已启用' : item.statusLabel}${item.description ? `\n${item.description}` : ''}`}
              onClick={() => toggle(item)}
            >
              <span className="capbar-row-icon" aria-hidden="true">
                <Icon name={item.icon} className="ico-sm" />
              </span>
              <span className="capbar-row-copy">
                <strong>
                  <span className="capbar-row-name">{item.name}</span>
                  <span className="capbar-row-type">{kindLabel(item.kind)}</span>
                </strong>
                <small>{item.status === 'ready' ? (item.description || '已启用') : item.statusLabel}</small>
              </span>
              <span className="capbar-row-state" aria-hidden="true">
                <Icon
                  name={selected ? 'i-check' : item.status === 'unavailable' ? 'i-alert' : item.status === 'loading' ? 'i-clock' : 'i-plus'}
                  className="ico-sm"
                />
              </span>
            </button>
          );
        })}
      </>
    );
  }

  return (
    <>
      <div className="capbar-pop-head">
        <div>
          <strong id={titleId}>添加能力</strong>
          <span>技能决定方法，插件提供工具。未选择时使用全部已启用能力。</span>
        </div>
        <button type="button" className="capbar-pop-close" aria-label="关闭能力面板" onClick={onClose}>
          <Icon name="i-x" className="ico-sm" />
        </button>
      </div>
      <label className="capbar-pop-search">
        <Icon name="i-search" className="ico-sm" />
        <input
          ref={searchRef}
          type="search"
          value={query}
          autoComplete="off"
          aria-label="搜索技能或插件"
          placeholder="搜索技能或插件"
          disabled={loading}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={onSearchKeyDown}
        />
      </label>
      <div className="capbar-pop-tabs" role="tablist" aria-label="筛选能力类型">
        {FILTERS.map(({value, label}) => (
          <button
            key={value}
            id={`${listId}-tab-${value}`}
            type="button"
            role="tab"
            className={filter === value ? 'capbar-pop-tab is-active' : 'capbar-pop-tab'}
            aria-selected={filter === value}
            aria-controls={listId}
            onClick={() => onFilterChange(value)}
            onKeyDown={(event) => onTabKeyDown(event, value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        ref={listRef}
        id={listId}
        className="capbar-pop-list"
        role="tabpanel"
        aria-labelledby={`${listId}-tab-${filter}`}
        aria-busy={loading || undefined}
        onKeyDown={onListKeyDown}
      >
        {body}
      </div>
      <div className="capbar-pop-foot">
        <span>点击即可添加或移除</span>
        <span className="capbar-pop-foot-actions">
          {onManage ? (
            <button type="button" className="capbar-link" onClick={onManage}>
              管理能力
            </button>
          ) : null}
          <span><kbd>Esc</kbd> 关闭</span>
        </span>
      </div>
      <span className="sr-only" role="status" aria-live="polite">{announcement}</span>
    </>
  );
}
