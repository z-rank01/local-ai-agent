import {useCallback, useEffect, useId, useMemo, useRef, useState} from 'react';
import {Icon} from '../ui/Icon';
import {Popover} from '../ui/Popover';
import {
  filterCapabilities,
  visibleCapabilities,
  type CapabilityFilter,
  type CapabilityItem,
  type ToggleCapability,
} from '../panels/capabilities/capabilityModel';
import {CapabilityPopover} from './CapabilityPopover';

export type CapabilityBarProps = {
  selected: CapabilityItem[];
  library: CapabilityItem[];
  loading?: boolean;
  error?: string;
  disabled?: boolean;
  maxVisible?: number;
  shortcutEnabled?: boolean;
  resetKey?: string;
  onToggle: ToggleCapability;
  onRetry?: () => void;
  onManage?: () => void;
  onOpenChange?: (open: boolean) => void;
};

export function CapabilityBar({
  selected,
  library,
  loading = false,
  error,
  disabled = false,
  maxVisible = 2,
  shortcutEnabled = true,
  resetKey,
  onToggle,
  onRetry,
  onManage,
  onOpenChange,
}: CapabilityBarProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<CapabilityFilter>('all');
  const [announcement, setAnnouncement] = useState('');
  const barRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const pendingChipFocusRef = useRef<string | null>(null);
  const titleId = useId();
  const listId = useId();
  const popoverId = useId();
  const shortcut = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)
    ? '⌘K'
    : 'Ctrl K';
  const {visible, overflow} = visibleCapabilities(selected, maxVisible);
  const selectedIds = useMemo(() => new Set(selected.map((item) => item.id)), [selected]);
  const filtered = useMemo(
    () => filterCapabilities(library, query, filter),
    [filter, library, query],
  );

  const changeOpen = useCallback((next: boolean) => {
    if (next && disabled) {
      return;
    }
    setOpen(next);
    if (!next) {
      setQuery('');
      setFilter('all');
      setAnnouncement('');
    }
    onOpenChange?.(next);
  }, [disabled, onOpenChange]);

  const closeAndRestore = useCallback(() => {
    changeOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus({preventScroll: true}));
  }, [changeOpen]);

  const close = useCallback(() => changeOpen(false), [changeOpen]);

  useEffect(() => {
    setOpen(false);
    setQuery('');
    setFilter('all');
    setAnnouncement('');
  }, [resetKey]);

  useEffect(() => {
    if (!disabled || !open) {
      return;
    }
    setOpen(false);
    setQuery('');
    setFilter('all');
    setAnnouncement('');
    onOpenChange?.(false);
  }, [disabled, onOpenChange, open]);

  useEffect(() => {
    if (!shortcutEnabled || disabled) {
      return;
    }
    const handler = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat) {
        return;
      }
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) {
        return;
      }
      if (event.key.toLowerCase() !== 'k') {
        return;
      }
      event.preventDefault();
      if (open) {
        changeOpen(false);
        window.requestAnimationFrame(() => triggerRef.current?.focus({preventScroll: true}));
      } else {
        changeOpen(true);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [changeOpen, disabled, open, shortcutEnabled]);

  useEffect(() => {
    if (!open || loading) {
      setAnnouncement('');
      return;
    }
    const timer = window.setTimeout(() => {
      if (!query.trim() && filter === 'all') {
        setAnnouncement('');
        return;
      }
      const suffix = filter === 'all' ? '项能力' : filter === 'skill' ? '项技能' : '项插件';
      setAnnouncement(`找到 ${filtered.length} ${suffix}`);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [filter, filtered, loading, open, query]);

  useEffect(() => {
    const target = pendingChipFocusRef.current;
    if (!target) {
      return;
    }
    pendingChipFocusRef.current = null;
    if (target === 'trigger') {
      triggerRef.current?.focus({preventScroll: true});
      return;
    }
    const chips = [...document.querySelectorAll<HTMLElement>('[data-capbar-chip]')];
    const node = chips.find((chip) => chip.dataset.capbarChip === target);
    (node ?? triggerRef.current)?.focus({preventScroll: true});
  }, [selected]);

  const removeChip = (item: CapabilityItem) => {
    const index = selected.findIndex((row) => row.id === item.id);
    const next = index >= 0 ? selected[index + 1] : undefined;
    pendingChipFocusRef.current = next?.id ?? 'trigger';
    void Promise.resolve(onToggle(item, false)).catch(() => {});
  };

  const manage = () => {
    changeOpen(false);
    onManage?.();
  };

  const emptyLabel = loading ? '正在读取能力…' : error ? '能力读取失败' : '尚未挂载能力';

  return (
    <div ref={barRef} className={disabled ? 'capbar is-disabled' : 'capbar'}>
      <div className="capbar-selected" role="group" aria-label="已选能力" aria-busy={loading || undefined}>
        {visible.map((item) => (
          <button
            key={item.id}
            type="button"
            className="capbar-chip"
            data-capbar-chip={item.id}
            title={`移除 ${item.name}${item.status === 'ready' ? '' : `（${item.statusLabel}）`}`}
            aria-label={`移除能力：${item.name}`}
            onClick={() => removeChip(item)}
          >
            <Icon name={item.icon} className="ico-sm" />
            <span className="capbar-chip-name">{item.name}</span>
            <span className="capbar-chip-x" aria-hidden="true"><Icon name="i-x" /></span>
          </button>
        ))}
        {overflow > 0 ? (
          <span
            className="capbar-overflow"
            title={`另外 ${overflow} 项：${selected.slice(visible.length).map((item) => item.name).join('、')}`}
          >
            <span aria-hidden="true">+{overflow}</span>
            <span className="sr-only">另外 {overflow} 项已选能力</span>
          </span>
        ) : null}
        {!selected.length ? <span className="capbar-empty">{emptyLabel}</span> : null}
      </div>
      <button
        ref={triggerRef}
        type="button"
        className="capbar-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        aria-label="添加或管理能力"
        title="添加或管理能力"
        disabled={disabled}
        onClick={() => changeOpen(!open)}
      >
        <Icon name="i-plus" className="ico-sm" />
        <span className="capbar-trigger-label">能力</span>
        <kbd className="capbar-kbd" aria-hidden="true">{shortcut}</kbd>
      </button>
      <Popover
        anchor={triggerRef}
        open={open}
        onClose={close}
        align="end"
        width={374}
        className="pop capbar-pop"
        labelledBy={titleId}
        id={popoverId}
        initialFocus={searchRef}
        trapFocus
        placementAnchor={barRef}
        upOffset={10}
      >
        <CapabilityPopover
          titleId={titleId}
          listId={listId}
          query={query}
          filter={filter}
          loading={loading}
          error={error}
          library={library}
          visible={filtered}
          selectedIds={selectedIds}
          announcement={announcement}
          searchRef={searchRef}
          onQueryChange={setQuery}
          onFilterChange={setFilter}
          onToggle={onToggle}
          onClose={closeAndRestore}
          onRetry={onRetry}
          onManage={onManage ? manage : undefined}
        />
      </Popover>
    </div>
  );
}
