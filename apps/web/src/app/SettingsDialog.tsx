import {useEffect, useId, useRef, useState} from 'react';
import type {CSSProperties, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent} from 'react';
import {patchModelSettings, saveProviderCredential} from '../api';
import {CODE_FONT_PRESETS, DENSITY_OPTIONS, UI_FONT_PRESETS} from '../lib/appearance';
import type {AppearanceSettings} from '../lib/appearance';
import type {ThemeMode} from '../lib/theme';
import type {AppStatus, ModelInfo, ProviderInfo} from '../types';
import {Dropdown} from '../ui/Dropdown';
import {Icon} from '../ui/Icon';
import {Switch} from '../ui/Switch';

export type SettingsTab = 'appearance' | 'model' | 'tools' | 'diagnostics';

export type SettingsDialogProps = {
  open: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
  appearance: AppearanceSettings;
  onAppearanceChange: (patch: Partial<AppearanceSettings>) => void;
  onAppearanceReset: () => void;
  model?: ModelInfo;
  status: AppStatus | null;
  providers: ProviderInfo[];
  busy: boolean;
  onSaved: () => void | Promise<void>;
};

type SettingsDialogBodyProps = SettingsDialogProps & {entered: boolean};

type ModelSettingChange = {
  thinking_enabled?: boolean | null;
  thinking_budget?: number | null;
  workspace_cloud_allowed?: boolean;
  workspace_cloud_write_allowed?: boolean;
};

type InlineNotice = {text: string; kind: 'ok' | 'error'};

const TABS: Array<{id: SettingsTab; title: string; icon: string}> = [
  {id: 'appearance', title: '外观', icon: 'i-sliders'},
  {id: 'model', title: '模型', icon: 'i-chip'},
  {id: 'tools', title: '工具与技能', icon: 'i-grid'},
  {id: 'diagnostics', title: '诊断', icon: 'i-pulse'},
];

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/* 全屏模态没有独立遮罩层：点在设置表面自身或布局容器的空白处即视为点遮罩 */
const SURFACE_CLASSES = ['settings', 'settings-main', 'settings-nav', 'settings-scroll'];

const RANGE_LIMITS = {
  contentFontSize: {min: 13, max: 19, step: 1},
  codeFontSize: {min: 12, max: 17, step: 0.5},
  readingWidth: {min: 420, max: 1120, step: 20},
  lineHeight: {min: 1.45, max: 2.05, step: 0.05},
} as const;

const THINKING_OPTIONS = [
  {value: 'auto', label: '自动'},
  {value: 'on', label: '开启'},
  {value: 'off', label: '关闭'},
];

const THINKING_BUDGET = {min: 128, max: 131072, step: 128};

const CAPABILITY_LABELS: Record<string, string> = {text: '文本', tools: '工具', streaming: '流式'};

const PROVIDER_KIND_LABELS: Record<string, string> = {cloud: '云端', local: '本地', ollama: '本地'};

/* 工具中文名取自 config/tools/*.yaml 的中文描述；未收录的工具回退原始标识符 */


function rangeFill(value: number, limit: {min: number; max: number}): CSSProperties {
  return {'--fill': `${Math.max(0, Math.min(100, (value - limit.min) / (limit.max - limit.min) * 100))}%`} as CSSProperties;
}
function isLocalModel(model: ModelInfo | undefined, providers: ProviderInfo[]): boolean {
  return providers.some(provider => provider.id === model?.provider_id && (provider.kind === 'local' || provider.kind === 'ollama'));
}
function errorText(error: unknown, fallback: string): string { return error instanceof Error ? error.message : fallback; }
function providerAvailability(provider: ProviderInfo): string {
  return !provider.enabled ? '已关闭' : provider.models.some(model => model.status === 'missing_key') ? '需要密钥' : '已配置';
}

function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((node) => node.offsetParent !== null);
}

export function SettingsDialog(props: SettingsDialogProps): JSX.Element | null {
  const {open} = props;
  const [entered, setEntered] = useState(false);

  /* 先挂载再补 .open，让原型的淡入位移过渡真正播放一次 */
  useEffect(() => {
    if (!open) {
      setEntered(false);
      return;
    }
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  if (!open) return null;
  return <SettingsDialogBody {...props} entered={entered} />;
}

function SettingsDialogBody({
  entered,
  onClose,
  initialTab = 'appearance',
  appearance,
  onAppearanceChange,
  onAppearanceReset,
  model,
  status,
  providers,
  busy,
  onSaved,
}: SettingsDialogBodyProps): JSX.Element {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const idPrefix = useId();

  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus({preventScroll: true});
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({preventScroll: true});
    };
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [tab]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const root = rootRef.current;
    if (!root) return;
    const items = focusableWithin(root);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || active === root) {
        event.preventDefault();
        last.focus();
      }
      return;
    }
    if (active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const onSurfaceClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (!SURFACE_CLASSES.some((name) => target.classList.contains(name))) return;
    /* 落在滚动条上的点击不算「点遮罩」，避免拖滚动条时误关 */
    const rect = target.getBoundingClientRect();
    if (event.clientX > rect.left + target.clientWidth || event.clientY > rect.top + target.clientHeight) return;
    onClose();
  };

  const activeTab = TABS.find((item) => item.id === tab) ?? TABS[0];

  return (
    <div
      ref={rootRef}
      className={entered ? 'settings open' : 'settings'}
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${idPrefix}-title`}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onClick={onSurfaceClick}
    >
      <div className="settings-top">
        <span className="settings-id">
          <span className="settings-title" id={`${idPrefix}-title`}>设置</span>
          <span className="settings-sub">{activeTab.title} · 改动立即生效</span>
        </span>
        <button ref={closeRef} type="button" className="tb-btn" aria-label="关闭设置" onClick={onClose}>
          <Icon name="i-x" />
        </button>
      </div>
      <div className="settings-main">
        <nav className="settings-nav" role="tablist" aria-label="设置分类">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              className="settings-nav-item"
              role="tab"
              id={`${idPrefix}-${item.id}`}
              aria-controls={`${idPrefix}-pane`}
              aria-selected={tab === item.id}
              onClick={() => setTab(item.id)}
            >
              <Icon name={item.icon} />
              {item.title}
            </button>
          ))}
        </nav>
        <div className="settings-scroll" ref={scrollRef}>
          <div
            className="settings-pane"
            role="tabpanel"
            id={`${idPrefix}-pane`}
            aria-labelledby={`${idPrefix}-${tab}`}
          >
            {tab === 'appearance' ? (
              <AppearancePane appearance={appearance} onChange={onAppearanceChange} onReset={onAppearanceReset} />
            ) : null}
            {tab === 'model' ? (
              <ModelPane model={model} status={status} providers={providers} busy={busy} onSaved={onSaved} />
            ) : null}
            {tab === 'tools' ? <ToolsPane catalog={status?.tool_catalog ?? []} connected={status !== null} /> : null}
            {tab === 'diagnostics' ? <DiagnosticsPane status={status} providers={providers} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function AppearanceRange({
  label,
  description,
  display,
  value,
  limit,
  onChange,
}: {
  label: string;
  description: string;
  display: string;
  value: number;
  limit: {min: number; max: number; step: number};
  onChange: (value: number) => void;
}): JSX.Element {
  return (
    <div className="field">
      <span className="set-row">
        <span className="field-label">{label}</span>
        <span className="set-val">{display}</span>
      </span>
      <input
        className="range"
        type="range"
        min={limit.min}
        max={limit.max}
        step={limit.step}
        value={value}
        aria-label={label}
        style={rangeFill(value, limit)}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <p className="setting-desc">{description}</p>
    </div>
  );
}

function AppearancePane({
  appearance,
  onChange,
  onReset,
}: {
  appearance: AppearanceSettings;
  onChange: (patch: Partial<AppearanceSettings>) => void;
  onReset: () => void;
}): JSX.Element {
  const [uiFontDraft, setUiFontDraft] = useState(appearance.uiFont);
  const [codeFontDraft, setCodeFontDraft] = useState(appearance.codeFont);

  useEffect(() => {
    setUiFontDraft(appearance.uiFont);
  }, [appearance.uiFont]);

  useEffect(() => {
    setCodeFontDraft(appearance.codeFont);
  }, [appearance.codeFont]);

  const uiPreset = UI_FONT_PRESETS.find((preset) => preset.value === appearance.uiFont)?.value ?? '';
  const codePreset = CODE_FONT_PRESETS.find((preset) => preset.value === appearance.codeFont)?.value ?? '';

  return (
    <>
      <div className="section-label">主题</div>
      <div className="field">
        <div>
          <div className="segmented theme-options" role="group" aria-label="主题">
            {([['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']] as Array<[ThemeMode, string]>).map(([value, label]) => (
              <button key={value} type="button" aria-pressed={appearance.theme === value} onClick={() => onChange({theme: value})}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="setting-desc">跟随系统会随系统外观自动切换；手动选择后保持当前主题。</p>
      </div>
      <div className="section-label">字体</div>
      <div className="field">
        <span className="field-label">界面字体</span>
        <Dropdown
          value={uiPreset}
          options={UI_FONT_PRESETS}
          placeholder="自定义"
          ariaLabel="界面字体预设"
          onChange={(value) => onChange({uiFont: value})}
        />
        <input
          className="input"
          type="text"
          value={uiFontDraft}
          aria-label="自定义界面字体"
          placeholder="也可填入本机字体系列名"
          spellCheck={false}
          onChange={(event) => setUiFontDraft(event.target.value)}
          onBlur={() => {
            const next = uiFontDraft.trim();
            if (!next) {
              setUiFontDraft(appearance.uiFont);
              return;
            }
            if (next !== appearance.uiFont) onChange({uiFont: next});
          }}
        />
        <p className="setting-desc">界面文字使用的字体，可直选预设或填本机字体系列名。</p>
      </div>
      <div className="field">
        <span className="field-label">代码字体</span>
        <Dropdown
          value={codePreset}
          options={CODE_FONT_PRESETS}
          placeholder="自定义"
          ariaLabel="代码字体预设"
          onChange={(value) => onChange({codeFont: value})}
        />
        <input
          className="input"
          type="text"
          value={codeFontDraft}
          aria-label="自定义代码字体"
          placeholder="也可填入本机等宽字体系列名"
          spellCheck={false}
          onChange={(event) => setCodeFontDraft(event.target.value)}
          onBlur={() => {
            const next = codeFontDraft.trim();
            if (!next) {
              setCodeFontDraft(appearance.codeFont);
              return;
            }
            if (next !== appearance.codeFont) onChange({codeFont: next});
          }}
        />
        <p className="setting-desc">代码块、路径与运行轨迹使用的等宽字体。</p>
      </div>
      <div className="section-label">排版</div>
      <AppearanceRange
        label="界面字号"
        display={`${appearance.contentFontSize} px`}
        value={appearance.contentFontSize}
        limit={RANGE_LIMITS.contentFontSize}
        description="界面正文、列表与按钮的基准字号。"
        onChange={(value) => onChange({contentFontSize: value})}
      />
      <AppearanceRange
        label="代码字号"
        display={`${appearance.codeFontSize} px`}
        value={appearance.codeFontSize}
        limit={RANGE_LIMITS.codeFontSize}
        description="代码块与等宽内容单独的字号。"
        onChange={(value) => onChange({codeFontSize: value})}
      />
      <AppearanceRange
        label="阅读宽度"
        display={`${appearance.readingWidth} px`}
        value={appearance.readingWidth}
        limit={RANGE_LIMITS.readingWidth}
        description="回答与报告正文的最大行宽，越窄越省眼。"
        onChange={(value) => onChange({readingWidth: value})}
      />
      <AppearanceRange
        label="行高"
        display={appearance.lineHeight.toFixed(2)}
        value={appearance.lineHeight}
        limit={RANGE_LIMITS.lineHeight}
        description="正文行间距倍数，长文阅读可调高。"
        onChange={(value) => onChange({lineHeight: value})}
      />
      <div className="field">
        <span className="field-label">界面密度</span>
        <div>
          <div className="segmented" role="group" aria-label="界面密度">
            {DENSITY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={appearance.density === option.value}
                onClick={() => onChange({density: option.value})}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <p className="setting-desc">控制列表与卡片的留白，不影响正文字号。</p>
      </div>
      <div className="panel-divider" />
      <div className="row-between">
        <span className="panel-hint">改动立即生效，并保存在本机。</span>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onReset}>
          <Icon name="i-refresh" className="ico-sm" />
          恢复默认
        </button>
      </div>
    </>
  );
}

function ModelPane({
  model,
  status,
  providers,
  busy,
  onSaved,
}: {
  model?: ModelInfo;
  status: AppStatus | null;
  providers: ProviderInfo[];
  busy: boolean;
  onSaved: () => void | Promise<void>;
}): JSX.Element {
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<InlineNotice | null>(null);
  const [budgetDraft, setBudgetDraft] = useState(model?.thinking_budget == null ? '' : String(model.thinking_budget));
  const [apiKey, setApiKey] = useState('');
  const [keySaving, setKeySaving] = useState(false);
  const [keyNotice, setKeyNotice] = useState<InlineNotice | null>(null);

  const budgetValue = model?.thinking_budget ?? null;

  useEffect(() => {
    setBudgetDraft(budgetValue == null ? '' : String(budgetValue));
  }, [model?.id, budgetValue]);

  if (!model) {
    return <p className="panel-hint">模型列表尚未加载，请稍后重新打开设置…</p>;
  }

  const local = isLocalModel(model, providers);
  const locked = busy || !status;
  const controlsDisabled = locked || saving;
  const thinkingValue = model.thinking_enabled == null ? 'auto' : model.thinking_enabled ? 'on' : 'off';
  const callsUsed = status?.model_calls_used ?? 0;
  const callsLimit = status?.model_call_limit ? String(status.model_call_limit) : '不限';

  const save = async (change: ModelSettingChange) => {
    if (!status || saving) return;
    setSaving(true);
    setNotice(null);
    try {
      await patchModelSettings({model_id: model.id, workspace_path: status.workspace_path,
        workspace_id: status.workspace_id, ...change});
      await onSaved();
      setNotice({text: '已保存，从下一条消息生效。', kind: 'ok'});
    } catch (error) {
      setNotice({text: errorText(error, '设置未保存'), kind: 'error'});
    } finally {
      setSaving(false);
    }
  };

  const commitBudget = () => {
    if (controlsDisabled) return;
    const raw = budgetDraft.trim();
    if (!raw) {
      if (budgetValue != null) void save({thinking_budget: null});
      return;
    }
    const value = Number(raw);
    if (!Number.isInteger(value) || value < THINKING_BUDGET.min || value > THINKING_BUDGET.max) {
      setNotice({text: `思考上限需为 ${THINKING_BUDGET.min} ~ ${THINKING_BUDGET.max} 的整数，未保存。`, kind: 'error'});
      setBudgetDraft(budgetValue == null ? '' : String(budgetValue));
      return;
    }
    if (value === budgetValue) return;
    void save({thinking_budget: value});
  };

  const saveKey = async () => {
    const key = apiKey.trim();
    if (!key || keySaving) return;
    setKeySaving(true);
    setKeyNotice(null);
    try {
      await saveProviderCredential(model.provider_id, key);
      setApiKey('');
      setKeyNotice({text: '已保存到本机私有配置，可直接开始对话。', kind: 'ok'});
      await onSaved();
    } catch (error) {
      setKeyNotice({text: errorText(error, 'API Key 未保存'), kind: 'error'});
    } finally {
      setKeySaving(false);
    }
  };

  return (
    <>
      <div className="model-card">
        <div className="row-between">
          <h4>{model.name}</h4>
          <span className="chip chip-quiet">{local ? '本地' : '云端'}</span>
        </div>
        <span className="meta">{model.provider_name} · {model.status === 'configured' ? '可用' : '待配置密钥'}</span>
        <div className="tag-row">
          {model.capabilities.map((capability) => CAPABILITY_LABELS[capability] ? (
            <span key={capability} className="chip chip-quiet">{CAPABILITY_LABELS[capability]}</span>
          ) : null)}
          {local ? null : (
            <span className="chip chip-quiet">
              {!status?.workspace_cloud_allowed ? '未授权' : status.cloud_write_allowed ? '可写入' : '仅只读'}
            </span>
          )}
          {model.context_window ? (
            <span className="chip chip-quiet chip-mono">{model.context_window} 上下文</span>
          ) : null}
        </div>
      </div>
      {!status ? (
        <div className="notice notice-err">
          <Icon name="i-alert" className="ico-sm" />
          <span>尚未连接到后端，暂时无法修改设置…</span>
        </div>
      ) : busy ? (
        <div className="notice notice-err">
          <Icon name="i-alert" className="ico-sm" />
          <span>正在生成回答，开关暂不可修改…</span>
        </div>
      ) : null}
      {notice ? (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          className={notice.kind === 'error' ? 'notice notice-err' : 'notice notice-ok'}
        >
          <Icon name={notice.kind === 'error' ? 'i-alert' : 'i-check'} className="ico-sm" />
          <span>{notice.text}</span>
        </div>
      ) : null}
      <div>
        <div className="section-label">推理</div>
        <div className="setting">
          <div className="setting-main">
            <div className="setting-name">推理过程</div>
            <div className="setting-desc">
              {model.thinking_supported
                ? '自动按供应商默认运行；开启或关闭会显式发送对应参数，按模型保存。'
                : '该模型未提供推理开关，按供应商默认行为运行；返回的推理内容仍会照常展示。'}
            </div>
          </div>
          {model.thinking_supported ? (
            <Dropdown
              value={thinkingValue}
              options={THINKING_OPTIONS}
              disabled={controlsDisabled}
              ariaLabel="推理过程"
              onChange={(value) => void save({thinking_enabled: value === 'auto' ? null : value === 'on'})}
            />
          ) : (
            <span className="chip chip-quiet">供应商默认</span>
          )}
        </div>
      </div>
      {model.thinking_budget_supported && model.thinking_enabled ? (
        <div className="field">
          <span className="set-row">
            <span className="field-label">思考上限</span>
            <span className="set-val">{budgetValue == null ? '不限制' : `${budgetValue} token`}</span>
          </span>
          <input
            className="input"
            type="number"
            inputMode="numeric"
            min={THINKING_BUDGET.min}
            max={THINKING_BUDGET.max}
            step={THINKING_BUDGET.step}
            placeholder="留空表示不限制"
            aria-label="思考上限"
            value={budgetDraft}
            disabled={controlsDisabled}
            onChange={(event) => setBudgetDraft(event.target.value)}
            onBlur={commitBudget}
          />
          <p className="setting-desc">
            思考 token 上限，可填 {THINKING_BUDGET.min} ~ {THINKING_BUDGET.max}；留空表示不限制，失焦后保存。
          </p>
        </div>
      ) : null}
      <div>
        <div className="section-label">云端授权</div>
        {local ? (
          <div className="notice notice-ok">
            <Icon name="i-check" className="ico-sm" />
            <span>本地模型可直接读写当前工作区，无需云端授权。</span>
          </div>
        ) : (
          <>
            <div className="setting">
              <div className="setting-main">
                <div className="setting-name">读取工作区</div>
                <div className="setting-desc">
                  允许云端列出与读取工作区文件、联网检索、查询历史会话；授权按工作区保存，关闭读取会同时撤销写入。
                </div>
              </div>
              <Switch
                checked={status?.workspace_cloud_allowed ?? false}
                disabled={controlsDisabled}
                label="读取工作区"
                onChange={(next) => void save({workspace_cloud_allowed: next})}
              />
            </div>
            {status?.workspace_cloud_allowed ? (
              <div className="setting">
                <div className="setting-main">
                  <div className="setting-name">写入与执行</div>
                  <div className="setting-desc">
                    允许云端改写与删除文件、执行命令、提交 git；关闭后仍保留只读权限。
                  </div>
                </div>
                <Switch
                  checked={status.cloud_write_allowed ?? false}
                  disabled={controlsDisabled}
                  label="写入与执行"
                  onChange={(next) => void save({workspace_cloud_write_allowed: next})}
                />
              </div>
            ) : null}
          </>
        )}
      </div>
      {local ? null : (
        <div>
          <div className="section-label">API Key</div>
          <div className="field">
            <p className="setting-desc">
              {model.status === 'configured' ? 'API Key 已配置，输入新密钥可替换。' : '尚未填写 API Key，填写后即可使用该供应商的模型。'}
            </p>
            <div className="field-inline">
              <input
                className="input grow"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                aria-label="API Key"
                placeholder="输入 API Key，仅存本机"
                disabled={keySaving}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <button
                type="button"
                className="btn btn-primary"
                disabled={!apiKey.trim() || keySaving}
                onClick={() => void saveKey()}
              >
                {keySaving ? '保存中…' : '保存'}
              </button>
            </div>
            <p className="setting-desc">仅保存到本机私有配置，不回显、不写入聊天与版本库。</p>
            {keyNotice ? (
              <p
                role={keyNotice.kind === 'error' ? 'alert' : 'status'}
                className={keyNotice.kind === 'error' ? 'notice notice-err' : 'notice notice-ok'}
              >
                <Icon name={keyNotice.kind === 'error' ? 'i-alert' : 'i-check'} className="ico-sm" />
                <span>{keyNotice.text}</span>
              </p>
            ) : null}
          </div>
        </div>
      )}
      <div>
        <div className="section-label">运行指标</div>
        <div className="row-between">
          <span className="panel-hint">本批云端请求（含失败尝试）</span>
          <span className="meta">{callsUsed} / {callsLimit}</span>
        </div>
      </div>
    </>
  );
}

function ToolsPane({catalog, connected}: {catalog: NonNullable<AppStatus['tool_catalog']>; connected: boolean}): JSX.Element {
  const groups = [...new Set(catalog.map(tool => tool.group))].map(name => ({name, items: catalog.filter(tool => tool.group === name)}));
  const tools = catalog;
  return (
    <>
      <div>
        <div className="section-label">能力清单 · {tools.length}</div>
        <p className="panel-hint">
          能力开关在右侧工作台的「技能」面板中管理；这里是本机已注册的工具，鼠标悬停可看到原始工具名。
        </p>
      </div>
      {groups.length ? (
        <div className="tool-groups">
          {groups.map((group) => (
            <div key={group.name}>
              <div className="tool-group-head">
                <span>{group.name}</span>
                <span className="num">{group.items.length}</span>
              </div>
              <div className="tool-chips">
                {group.items.map((tool) => (
                  <span key={tool.name} className="tool-chip" title={tool.name}>{tool.display_name}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="panel-hint">{connected ? '当前没有已注册的工具。' : '尚未连接到后端，工具清单暂时不可用…'}</p>
      )}
    </>
  );
}

function DiagnosticsPane({status, providers}: {status: AppStatus | null; providers: ProviderInfo[]}): JSX.Element {
  const online = status !== null;
  return (
    <>
      <div>
        <div className="section-label">服务</div>
        <div className="field">
          <div className="online-row">
            <span className={online ? 'dot ok' : 'dot warn'} aria-hidden="true" />
            <span>{online ? '后端在线' : '后端离线'}</span>
          </div>
          <p className="panel-hint">
            {online
              ? '关闭页面约 15 秒后会自动停止全部本地服务。'
              : '界面数据可能已过期；等待自动重连，或刷新页面后重试。'}
          </p>
        </div>
      </div>
      <div>
        <div className="section-label">模型提供方</div>
        {providers.length ? providers.map((provider) => (
          <div className="prov-row" key={provider.id}>
            <span>{provider.name}</span>
            <span className="chip chip-quiet">
              {PROVIDER_KIND_LABELS[provider.kind] ?? '类型未知'} · {provider.models.length} 个模型 · {providerAvailability(provider)}
            </span>
          </div>
        )) : <p className="panel-hint">尚未读取到模型提供方…</p>}
      </div>
      <div>
        <div className="section-label">工作区路径</div>
        <p className="mono-block">{status?.workspace_path ?? '尚未连接，暂时无法读取。'}</p>
      </div>
    </>
  );
}
