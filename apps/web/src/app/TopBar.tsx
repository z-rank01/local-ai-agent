import {useEffect, useId, useRef, useState} from 'react';
import {Icon} from '../ui/Icon';
import {Popover} from '../ui/Popover';
import {ModelPicker} from './ModelPicker';
import type {ProviderInfo} from '../types';

export type ExportFormat = 'markdown' | 'json' | 'txt';

export type TopBarProps = {
  title: string;
  onOpenDrawer: () => void;
  providers: ProviderInfo[];
  value: string;
  onChangeModel: (modelId: string) => void;
  onRefreshModels: () => void | Promise<void>;
  modelsRefreshing: boolean;
  exportEnabled: boolean;
  onExport: (format: ExportFormat) => void;
  onOpenModelSettings: () => void;
  sheetOpen: boolean;
  onToggleSheet: () => void;
  onOpenSettings: () => void;
};

const EXPORT_ITEMS: {format: ExportFormat; label: string; icon: string}[] = [
  {format: 'markdown', label: 'Markdown', icon: 'i-doc'},
  {format: 'json', label: 'JSON', icon: 'i-list'},
  {format: 'txt', label: '纯文本', icon: 'i-file'},
];

/* 顶栏：纯色实底细条。左会话标题（开抽屉）→ 中模型胶囊 → 右图标组
   （导出 / 模型设置 / 设置 / 工作台）。模型选择器自带触发器与浮层。 */
export function TopBar({
  title,
  onOpenDrawer,
  providers,
  value,
  onChangeModel,
  onRefreshModels,
  modelsRefreshing,
  exportEnabled,
  onExport,
  onOpenModelSettings,
  sheetOpen,
  onToggleSheet,
  onOpenSettings,
}: TopBarProps): JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);
  const exportRef = useRef<HTMLButtonElement | null>(null);
  const exportLabelId = useId();

  /* 没有可导出的会话（或被删空）时立刻收起菜单，避免下次恢复时自己弹出来 */
  useEffect(() => {
    if (!exportEnabled) {
      setMenuOpen(false);
    }
  }, [exportEnabled]);

  return (
    <header className="topbar">
      <button
        type="button"
        className="tb-title"
        aria-haspopup="dialog"
        title="打开会话列表"
        onClick={onOpenDrawer}
      >
        <span className="tb-title-in">
          <Icon name="i-list" className="ico-sm" />
          <span>{title}</span>
        </span>
      </button>
      <ModelPicker
        providers={providers}
        value={value}
        onChange={onChangeModel}
        onRefresh={onRefreshModels}
        refreshing={modelsRefreshing}
        onOpenSettings={onOpenModelSettings}
      />
      <button
        ref={exportRef}
        type="button"
        className="tb-btn"
        aria-haspopup="menu"
        aria-expanded={exportEnabled && menuOpen}
        aria-disabled={exportEnabled ? undefined : true}
        disabled={!exportEnabled}
        title={exportEnabled ? '导出当前会话' : '当前没有可导出的会话'}
        aria-label={exportEnabled ? '导出当前会话' : '当前没有可导出的会话'}
        onClick={() => setMenuOpen((current) => !current)}
      >
        <Icon name="i-download" />
      </button>
      <button
        type="button"
        className="tb-btn"
        title="模型设置"
        aria-label="模型设置"
        onClick={onOpenModelSettings}
      >
        <Icon name="i-chip" />
      </button>
      <button
        type="button"
        className="tb-btn"
        title="设置"
        aria-label="设置"
        onClick={onOpenSettings}
      >
        <Icon name="i-gear" />
      </button>
      <button
        type="button"
        className="tb-btn"
        aria-expanded={sheetOpen}
        title="工作台 · 技能与工作区"
        aria-label="打开工作台"
        onClick={onToggleSheet}
      >
        <Icon name="i-panel" />
      </button>
      <Popover
        anchor={exportRef}
        open={exportEnabled && menuOpen}
        onClose={() => setMenuOpen(false)}
        labelledBy={exportLabelId}
      >
        <div className="pop-label" id={exportLabelId}>导出当前会话</div>
        {EXPORT_ITEMS.map((item) => (
          <button
            key={item.format}
            type="button"
            className="pop-item"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              onExport(item.format);
            }}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </button>
        ))}
      </Popover>
    </header>
  );
}
