import {useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent} from 'react';

export type PickerModel = {
  id: string;
  name: string;
  provider_id: string;
  provider_name: string;
  status: string;
};

export type PickerProvider = {
  id: string;
  name: string;
  kind: string;
  models: PickerModel[];
};

export function ModelPicker({providers, value, onChange, onRefresh, refreshing, ariaLabel = '选择模型', disabled = false}: {
  providers: PickerProvider[];
  value: string;
  onChange: (modelId: string) => void;
  onRefresh: () => void | Promise<void>;
  refreshing: boolean;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState('');
  const pickerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const allModels = providers.flatMap((provider) => provider.models);
  const selectedModel = allModels.find((model) => model.id === value);
  const closeMenu = (restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (pickerRef.current && target && !pickerRef.current.contains(target)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMenu();
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);
  const query = filter.trim().toLowerCase();
  const visibleGroups = providers
    .map((provider) => ({
      provider,
      models: query ? provider.models.filter((model) => model.name.toLowerCase().includes(query)) : provider.models,
    }))
    .filter((group) => !query || group.models.length > 0);
  const focusOption = (option: HTMLButtonElement | null | undefined) => option?.focus();
  const moveOptionFocus = (delta: number) => {
    const options = menuRef.current?.querySelectorAll<HTMLButtonElement>('.model-picker-option:not([disabled])');
    if (!options || options.length === 0) return;
    const list = Array.from(options);
    const current = list.findIndex((option) => option === document.activeElement);
    focusOption(list[(current + delta + list.length) % list.length]);
  };
  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveOptionFocus(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveOptionFocus(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusOption(menuRef.current?.querySelector<HTMLButtonElement>('.model-picker-option:not([disabled])'));
    } else if (event.key === 'End') {
      event.preventDefault();
      const options = menuRef.current?.querySelectorAll<HTMLButtonElement>('.model-picker-option:not([disabled])');
      focusOption(options?.[options.length - 1]);
    }
  };
  return (
    <div className="model-picker" ref={pickerRef}>
      <button type="button" className="model-picker-trigger" aria-haspopup="listbox" ref={triggerRef}
        aria-label={`${ariaLabel}：${selectedModel ? `${selectedModel.provider_name} / ${selectedModel.name}` : value || '未选择模型'}`}
        aria-expanded={open} disabled={disabled || !providers.length}
        onClick={() => setOpen((current) => !current)}>
        <span>{selectedModel ? `${selectedModel.provider_name} / ${selectedModel.name}` : value ? `${value} · 当前不可用` : '未选择模型'}</span>
        <span aria-hidden="true" className="model-picker-chevron">⌄</span>
      </button>
      {open ? (
        <div className="model-picker-menu" role="listbox" aria-label={ariaLabel} ref={menuRef} onKeyDown={handleMenuKeyDown}>
          {allModels.length > 6 ? (
            <input className="model-picker-filter" type="search" value={filter} aria-label="搜索模型"
              placeholder="搜索模型…" onChange={(event) => setFilter(event.target.value)} />
          ) : null}
          <div className="model-picker-groups">
            {visibleGroups.map(({provider, models}) => {
              const isCollapsed = !query && (collapsed[provider.id] ?? provider.id !== selectedModel?.provider_id);
              const missingKey = models.length > 0 && models.every((model) => model.status === 'missing_key');
              return (
                <div key={provider.id} className="model-picker-group" role="group" aria-label={provider.name}>
                  <button type="button" className="model-picker-group-header" aria-expanded={!isCollapsed}
                    onClick={() => setCollapsed((current) => ({...current, [provider.id]: !isCollapsed}))}>
                    <span aria-hidden="true">{isCollapsed ? '▸' : '▾'}</span>
                    <span>{provider.name}</span>
                    <em>{provider.kind === 'cloud' ? '云端' : '本地'} · {models.length}</em>
                    {missingKey ? <em>待配置密钥</em> : null}
                  </button>
                  {isCollapsed ? null : models.map((model) => (
                    <button type="button" key={model.id} role="option"
                      aria-selected={model.id === selectedModel?.id}
                      className={`model-picker-option${model.id === selectedModel?.id ? ' active' : ''}`}
                      disabled={model.status !== 'configured'}
                      title={model.status === 'missing_key' ? '请先在“模型设置”中配置密钥' : undefined}
                      onClick={() => { onChange(model.id); closeMenu(); }}>
                      <span>{model.name}{model.status === 'missing_key' ? ' · 待配置密钥' : ''}</span>
                      {model.id === selectedModel?.id ? <em>当前</em> : null}
                    </button>
                  ))}
                </div>
              );
            })}
            {visibleGroups.length === 0 ? <p className="model-picker-empty">没有匹配“{filter.trim()}”的模型</p> : null}
          </div>
          <button type="button" className="model-picker-refresh" disabled={refreshing}
            onClick={() => void onRefresh()}>{refreshing ? '正在刷新…' : '刷新模型列表'}</button>
        </div>
      ) : null}
    </div>
  );
}
