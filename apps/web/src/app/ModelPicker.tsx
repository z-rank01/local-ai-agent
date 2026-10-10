import {useId, useMemo, useRef, useState} from 'react';
import type {KeyboardEvent as ReactKeyboardEvent} from 'react';
import {Icon} from '../ui/Icon';
import {Popover} from '../ui/Popover';
import type {ModelInfo, ProviderInfo} from '../types';

export type ModelPickerProps = {
  providers: ProviderInfo[];
  value: string;
  onChange: (modelId: string) => void;
  onRefresh: () => void | Promise<void>;
  refreshing: boolean;
  disabled?: boolean;
  onOpenSettings?: () => void;
};

/* 上下文窗口按 K 记（32768 → 32K），未知则不显示，不编造数值。 */
function contextLabel(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return '';
  }
  return value >= 1024 ? `${Math.round(value / 1024)}K` : String(value);
}

const isUsable = (model: ModelInfo): boolean => model.status === 'configured';

/* 模型选择器：触发器（.model-pill）与浮层都在本组件内，定位/关闭/回焦交给 Popover；
   键盘走选项按钮本身，方向键只做焦点移动，选择仍是按钮的 Enter/Space。 */
export function ModelPicker({
  providers,
  value,
  onChange,
  onRefresh,
  refreshing,
  disabled = false,
  onOpenSettings,
}: ModelPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const listId = useId();

  const allModels = useMemo(() => providers.flatMap((provider) => provider.models), [providers]);
  const selected = allModels.find((model) => model.id === value);

  /* 搜索同时匹配模型名、模型标识与供应商名：名字里没有的型号，用户常直接粘贴标识。 */
  const groups = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return providers
      .map((provider) => ({
        provider,
        models: query
          ? provider.models.filter((model) =>
              `${model.name}${model.id}${provider.name}`.toLowerCase().includes(query))
          : provider.models,
      }))
      .filter((group) => group.models.length > 0);
  }, [filter, providers]);

  /* 方向键只在「展开且可用」的条目间走：折叠组的内容仍留在 DOM（grid 折叠），
     不排除的话焦点会落到被裁掉的行上。 */
  const options = useMemo(
    () => groups
      .filter((group) => collapsed[group.provider.id] !== true)
      .flatMap((group) => group.models.filter(isUsable)),
    [collapsed, groups],
  );

  const moveFocus = (event: ReactKeyboardEvent<HTMLElement>, step: number | 'first' | 'last') => {
    const total = options.length;
    if (!total) return;
    event.preventDefault();
    if (step === 'first') {
      optionRefs.current[0]?.focus();
      return;
    }
    if (step === 'last') {
      optionRefs.current[total - 1]?.focus();
      return;
    }
    const current = optionRefs.current.findIndex((node) => node === document.activeElement);
    const next = current < 0
      ? (step > 0 ? 0 : total - 1)
      : (current + step + total) % total;
    optionRefs.current[next]?.focus();
  };

  const onNavKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === 'ArrowDown') moveFocus(event, 1);
    else if (event.key === 'ArrowUp') moveFocus(event, -1);
    else if (event.key === 'Home') moveFocus(event, 'first');
    else if (event.key === 'End') moveFocus(event, 'last');
  };

  const pick = (modelId: string) => {
    setOpen(false);
    onChange(modelId);
  };

  const missingKey = selected ? !isUsable(selected) : false;
  const pendingCount = allModels.filter((model) => !isUsable(model)).length;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="model-pill"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={selected ? `选择模型：${selected.provider_name} / ${selected.name}` : '选择模型'}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="dot" data-state={missingKey ? 'missing' : 'ok'} />
        <span className="pill-name">{selected ? selected.name : '未选择模型'}</span>
        <Icon name="i-chev-down" className="ico-sm" />
      </button>
      <Popover
        anchor={triggerRef}
        open={open}
        onClose={() => setOpen(false)}
        align="start"
        className="pop picker"
      >
        <div className="picker-search" onKeyDown={onNavKeyDown}>
          <Icon name="i-search" className="ico-sm" />
          <input
            className="input input-sm"
            type="search"
            autoComplete="off"
            placeholder="搜索模型…"
            aria-label="搜索模型"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>
        <div
          className="picker-list"
          id={listId}
          role="listbox"
          aria-label="选择模型"
          onKeyDown={onNavKeyDown}
        >
          {groups.length === 0 ? (
            <p className="pop-label">
              {filter.trim() ? `没有匹配「${filter.trim()}」的模型` : '暂无可用模型，点右下角刷新。'}
            </p>
          ) : groups.map(({provider, models}) => {
            const isCollapsed = collapsed[provider.id] === true;
            return (
              <div
                key={provider.id}
                className="picker-group"
                data-collapsed={isCollapsed ? 'true' : 'false'}
              >
                <button
                  type="button"
                  className="picker-group-head"
                  aria-expanded={!isCollapsed}
                  onClick={() => setCollapsed((current) => ({...current, [provider.id]: !isCollapsed}))}
                >
                  <b>{provider.name}</b>
                  <span>{provider.kind === 'cloud' ? '云端' : '本地'} · {models.length}</span>
                  <Icon name="i-chev-down" className="ico-sm" />
                </button>
                <div className="picker-options">
                  <div className="picker-options-in">
                    {models.map((model) => {
                      const usable = isUsable(model);
                      const isSelected = model.id === value;
                      const context = contextLabel(model.context_window) + (model.context_capacity_source === 'conservative_fallback' ? '（保守配置）' : '');
                      return (
                        <button
                          key={model.id}
                          ref={(node) => {
                            const index = options.indexOf(model);
                            if (index >= 0) optionRefs.current[index] = node;
                          }}
                          type="button"
                          className="picker-opt"
                          role="option"
                          aria-selected={isSelected}
                          aria-disabled={usable ? undefined : true}
                          disabled={!usable}
                          title={usable ? undefined : '待配置密钥，请先在“模型设置”中配置'}
                          onClick={() => pick(model.id)}
                        >
                          <span className="picker-opt-main">
                            <span className="picker-opt-name">{model.name}</span>
                            <span className="picker-opt-sub">
                              {usable ? `${model.id} · 可用` : `${model.id} · 待配置密钥`}
                            </span>
                          </span>
                          {isSelected
                            ? <Icon name="i-check" className="ico-sm" />
                            : context ? <span className="chip chip-quiet">{context}</span> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="picker-foot">
          <span className="meta">
            {allModels.length} 个模型{pendingCount ? ` · ${pendingCount} 个待配置密钥` : ''}
          </span>
          <span className="row-lead">
            {onOpenSettings ? (
              <button
                className="btn btn-quiet btn-sm"
                type="button"
                onClick={() => {
                  setOpen(false);
                  onOpenSettings();
                }}
              >
                模型设置
              </button>
            ) : null}
            <button
              className="btn btn-quiet btn-icon btn-sm"
              type="button"
              title="刷新模型列表"
              aria-label="刷新模型列表"
              disabled={refreshing}
              onClick={() => void onRefresh()}
            >
              <Icon name="i-refresh" className={refreshing ? 'ico-sm spin' : 'ico-sm'} />
            </button>
          </span>
        </div>
      </Popover>
    </>
  );
}
