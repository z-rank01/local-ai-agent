import {useId, useRef, useState} from 'react';
import {changeManagedSkill, prepareCapability, setCapability} from '../../api';
import {changePackage, importPackage} from '../../extensions/packages';
import {ContributionSettings, hasContribution} from '../../extensions/ContributionPanels';
import type {CapabilityPanelProps} from '../../extensions/contracts';
import {Collapse} from '../../ui/Collapse';
import {Switch} from '../../ui/Switch';
import {Popover} from '../../ui/Popover';
import {Icon} from '../../ui/Icon';
import {askConfirm} from '../../lib/confirm';
import {confirmCapabilityLeave} from '../../lib/capabilityLeave';
import {formatBackendError} from '../../lib/format';
import {notify} from '../../lib/notify';
import {capabilityIcon} from './capabilityModel';
import {entryState, type CapabilityEntry as Entry} from './inventory';
import {
  capabilityDisplayDescription,
  capabilityDisplayName,
  capabilityError,
  capabilityOrigin,
  capabilitySource,
} from './capabilityManagerModel';
import {SkillSourceEditor} from './SkillSourceEditor';
import {SkillVersions} from './SkillVersions';
import {PluginVersions} from './PluginVersions';

export function CapabilityEntry({entry, workspaceId, open, onOpen, refresh, variant = 'legacy',
  selected = false, onSelectedChange, ...props}: CapabilityPanelProps & {
  entry: Entry; workspaceId: string; open: boolean; onOpen: () => void; refresh: () => Promise<void>;
  variant?: 'legacy' | 'v2'; selected?: boolean; onSelectedChange?: (selected: boolean) => void;
}) {
  const [tab, setTab] = useState('概览');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [menu, setMenu] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const switchRef = useRef<HTMLButtonElement>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const detailId = useId();
  const state = entryState(entry);
  const {plugin, skill, install} = entry;
  const displayName = capabilityDisplayName(entry);
  const v2 = variant === 'v2';
  const sourcePath = (install?.active ?? install?.pending ?? install?.history[0])?.source ?? `plugins/${entry.id}`;
  const legacySource = entry.kind === 'skill'
    ? skill?.active?.kind === 'python' ? '旧式 Python 兼容包' : '工作区目录包'
    : install ? '用户导入' : '内置';
  const metaSource = capabilitySource(entry);
  const metaOrigin = capabilityOrigin(entry);
  const persistentError = capabilityError(entry);
  const tabs = ['概览', ...(entry.kind === 'plugin' && hasContribution(entry.id) ? ['设置'] : []),
    ...(skill ? ['文件'] : []), '版本'];
  const canToggle = skill ? !skill.missing : Boolean(plugin?.actions?.toggle);
  const transitional = state.label === '启动中' || state.label === '等待收尾';
  const switchDisabled = Boolean(busy) || !canToggle || transitional;
  const updating = install?.deployment === 'PENDING_RESTART' || install?.deployment === 'BUILD_REQUIRED';
  const failedUpdate = install?.deployment === 'BUILD_FAILED' || install?.deployment === 'DEPLOY_FAILED';
  const stateLabels: Record<string, string> = {ACTIVE:'已激活', INACTIVE:'已停用', STARTING:'启动中', DRAINING:'等待收尾', START_FAILED:'启动失败', STOP_FAILED:'收尾未确认'};
  const environmentLabels: Record<string, string> = {NOT_PREPARED:'尚未准备', PREPARING:'准备中', PREPARED:'已准备', PREPARE_FAILED:'准备失败', INTERRUPTED:'准备中断'};

  async function selectTab(next: string) {
    if (await confirmCapabilityLeave()) setTab(next);
  }

  async function perform(action: () => Promise<unknown>, label = '处理中',
    successMessage = '操作已确认；生效条件见卡片状态', afterAction?: () => void): Promise<boolean> {
    if (!await confirmCapabilityLeave()) return false;
    setBusy(label); setError(''); setMenu(false);
    try {
      await action();
      // Remove the task selection as soon as the disable is accepted; the
      // registry refresh can be slow because it freezes the Skill runner.
      afterAction?.();
      await refresh();
      props.onChanged?.();
      if (successMessage) notify(successMessage);
      return true;
    } catch (cause) {
      const message = formatBackendError(cause);
      setError(message);
      await refresh();
      notify(message, 'error');
      return false;
    } finally {
      setBusy('');
    }
  }

  function skillAction(action: string, revision?: string) {
    void perform(() => changeManagedSkill(workspaceId, entry.id, action, revision));
  }

  async function toggle(next: boolean) {
    if (!next && entry.id === 'finance' && !await askConfirm({
      title: '停用金融研究', body: '后台任务会在安全边界收尾；这不会删除报告或替你确认账户。', okLabel: '停用', danger: true,
    })) return;
    const wasSelected = v2 && selected && state.enabled;
    const success = await perform(
      () => entry.kind === 'plugin'
        ? setCapability(entry.id, next)
        : changeManagedSkill(workspaceId, entry.id, next ? 'enable' : 'disable'),
      next ? '正在启用' : '等待收尾',
      '',
      () => { if (!next && wasSelected) onSelectedChange?.(false); },
    );
    if (!success) return;
    const message = next
      ? `已启用「${displayName}」`
      : wasSelected
        ? `已停用「${displayName}」，并从当前对话移除`
        : `已停用「${displayName}」`;
    const undo = async () => {
      try {
        setBusy('正在恢复');
        const target = !next;
        await (entry.kind === 'plugin'
          ? setCapability(entry.id, target)
          : changeManagedSkill(workspaceId, entry.id, target ? 'enable' : 'disable'));
        await refresh();
        if (wasSelected && target) onSelectedChange?.(true);
        // Undoing an enable is a disable; never leave a disabled id selected.
        if (!target && selectedRef.current) onSelectedChange?.(false);
        notify('已恢复');
        window.requestAnimationFrame(() => switchRef.current?.focus({preventScroll: true}));
      } catch (cause) {
        const message = formatBackendError(cause);
        await refresh().catch(() => undefined);
        notify(message, 'error');
      } finally {
        setBusy('');
      }
    };
    notify(message, 'ok', {label: '撤销', run: undo});
    window.requestAnimationFrame(() => switchRef.current?.focus({preventScroll: true}));
  }

  async function remove() {
    const accepted = await askConfirm({title: skill ? '移出能力目录？' : '卸载安装副本？',
      body: skill ? '保留源文件、历史快照和成果，之后可从历史记录重新启用。' : '保留包快照及成果。正常退出后从启动.bat 重启才会移除安装副本。',
      okLabel: skill ? '移出目录' : '安排卸载', danger: true});
    if (accepted) void perform(() => skill ? changeManagedSkill(workspaceId, entry.id, 'uninstall') : changePackage(entry.id, 'uninstall'));
  }

  const legacyHead = (
    <div className="cap-entry-head">
      <button ref={trigger} className="cap-entry-title" onClick={onOpen} aria-expanded={open}
        aria-controls={detailId} aria-label={`${open ? '收起' : '展开'}${entry.title}详情`}>
        <strong>{entry.title}</strong>
      </button>
      <div className="cap-entry-controls">
        <Switch label={`启用${entry.title}`} checked={state.enabled} disabled={Boolean(busy) || !canToggle}
          onChange={next => void toggle(next)} />
        <button ref={anchor} className="tb-btn" aria-label={`${entry.title}更多操作`} aria-expanded={menu}
          onClick={() => setMenu(!menu)}><Icon name="i-menu" /></button>
      </div>
      <div className="cap-entry-summary"><p className="panel-hint cap-description">{entry.description}</p>
        <div className="cap-meta"><span className={`chip chip-${busy ? 'warn' : state.tone}`} role="status">{busy || state.label}</span>
          <span>{entry.kind === 'plugin' ? 'Plugin · 本机能力' : 'Skill · 当前工作区'} · {legacySource}</span></div>
      </div>
    </div>
  );

  const v2Head = (
    <div className="capm-row">
      <button ref={trigger} type="button" className="capm-row-open" onClick={onOpen}
        aria-expanded={open} aria-controls={detailId}
        aria-label={`${open ? '收起' : '展开'}${displayName}详情${selected && state.enabled ? '，当前对话已选' : ''}${state.tone === 'warn' || state.tone === 'danger' ? `，${state.label}` : ''}`}>
        <span className="capm-row-icon" aria-hidden="true"><Icon name={capabilityIcon(entry.id, entry.kind)} /></span>
        <span className="capm-row-body">
          <span className="capm-row-titleline">
            <strong className="capm-row-title">{displayName}</strong>
            {selected && state.enabled && <span className="capm-badge capm-badge-context">对话中</span>}
            {(busy || state.tone === 'warn' || state.tone === 'danger') && (
              <span className={`capm-badge capm-badge-${busy ? 'warn' : state.tone}`}
                title={persistentError || state.label}>
                <Icon name="i-alert" className="ico-sm" />
                {busy || state.label}
              </span>
            )}
          </span>
          <span className="capm-row-desc">{capabilityDisplayDescription(entry)}</span>
          <span className="capm-row-meta">{entry.kind === 'plugin' ? '插件' : '技能'}
            <span className="capm-meta-sep">·</span>{metaSource}
            <span className="capm-meta-sep">·</span>{metaOrigin}</span>
        </span>
        <Icon name="i-chev-down" className="ico-sm capm-row-chev" />
      </button>
      <span className="capm-row-controls">
        <span className="capm-switch-hit">
          <Switch buttonRef={switchRef} label={`${state.enabled ? '停用' : '启用'}「${displayName}」`}
            checked={state.enabled} disabled={switchDisabled} onChange={next => void toggle(next)} />
        </span>
        <button ref={anchor} type="button" className="capm-row-more" aria-label={`${displayName}更多操作`}
          aria-expanded={menu} onClick={() => setMenu(!menu)}><Icon name="i-menu" /></button>
      </span>
    </div>
  );

  const detailNotes = variant === 'v2' && (
    <>
      {error && <p className="run-error capm-detail-note" role="alert">{error}</p>}
      {updating && <p className="panel-hint capm-detail-note">{install?.deployment === 'BUILD_REQUIRED' ? '等待启动器构建确认' : '已安排变更，正常退出后重启生效'}</p>}
      {!updating && !failedUpdate && (install?.pending || skill?.pending) && <p className="panel-hint capm-detail-note">有待审阅版本，请在“版本”中查看差异与生效条件。</p>}
      {failedUpdate && <p className="panel-hint capm-detail-note">{state.enabled ? '当前版本可用 · ' : ''}上次更新失败；候选版本与错误保留在详情中。</p>}
    </>
  );

  return <article className={v2 ? 'cap-entry cap-entry-v2' : 'cap-entry'} data-open={open ? 'true' : 'false'}
    data-capability-key={entry.key} role={v2 ? 'listitem' : undefined}>
    {v2 ? v2Head : legacyHead}
    {!v2 && updating && <p className="panel-hint cap-inline">{install?.deployment === 'BUILD_REQUIRED' ? '等待启动器构建确认' : '已安排变更，正常退出后重启生效'}</p>}
    {!v2 && !updating && !failedUpdate && (install?.pending || skill?.pending) && <p className="panel-hint cap-inline">有待审阅版本，请在“版本”中查看差异与生效条件。</p>}
    {!v2 && failedUpdate && <p className="panel-hint cap-inline">{state.enabled ? '当前版本可用 · ' : ''}上次更新失败；候选版本与错误保留在详情中。</p>}
    {!v2 && error && <p className="run-error cap-inline" role="alert">{error}</p>}
    <Popover anchor={anchor} open={menu} onClose={() => setMenu(false)}>
      <div role="menu" aria-label={`${displayName}操作`} className="cap-menu">
        <button role="menuitem" disabled={!!busy} onClick={() => {setMenu(false); if (!open) onOpen(); else trigger.current?.focus();}}>查看详情</button>
        <button role="menuitem" disabled={!!busy} onClick={() => void perform(install?.active ? () => importPackage(install.active!.source) : refresh)}>{skill ? '检查目录与更新' : install?.active ? '从原来源检查更新' : '刷新状态'}</button>
        <button role="menuitem" disabled={!!busy || (!skill && !install)} onClick={() => {setMenu(false); void remove();}}>{skill ? '移出能力目录' : '卸载安装副本'}</button>
        {!skill && !install && <p className="panel-hint">内置插件随项目部署更新，可停用。</p>}
      </div>
    </Popover>
    <Collapse id={detailId} open={open} className={v2 ? 'cap-body capm-body' : 'cap-body'}>
      <div className={v2 ? 'cap-detail capm-detail' : 'cap-detail'}>
        {detailNotes}
        <div className="cap-tabs" role="tablist" aria-label={`${displayName}详情栏目`}>{tabs.map((label, index) => <button key={label} role="tab" aria-selected={tab === label} tabIndex={tab === label ? 0 : -1}
          onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            void selectTab(tabs[next]); (event.currentTarget.parentElement?.querySelectorAll('button')[next] as HTMLElement)?.focus();
          }} onClick={() => void selectTab(label)}>{label}</button>)}</div>
        {<div className="cap-tab-content" role="tabpanel" aria-label={tab}>
          {tab === '概览' && <>
            <p className="panel-hint">{entry.description}</p>
            <p className="panel-hint">ID：{entry.id}<br/>版本：{plugin?.version ?? skill?.active?.version ?? install?.active?.manifest.version ?? '未标注'}</p>
            <p className="panel-hint">权限：{(plugin?.permissions ?? skill?.pending?.permissions ?? skill?.active?.permissions ?? []).join('、') || '无额外声明'}<br/>
              必需依赖：{(plugin?.required ?? skill?.active?.dependencies ?? []).join('、') || '无'}<br/>可选依赖：{plugin?.optional?.join('、') || '无'}</p>
            <p className="panel-hint">{skill ? '启停和版本选择影响后续运行；在途运行保持固定版本。移出目录保留原件和历史。' : plugin?.action_note ?? '本地包需审阅并确认版本，正常退出后部署构建。'}</p>
            {(plugin?.error || install?.error || skill?.candidate_error) && <p className="run-error">{plugin?.error || install?.error || skill?.candidate_error}</p>}
            {Object.entries(plugin?.lifecycle ?? {}).map(([owner, row]) => <p className="panel-hint" key={owner}>{stateLabels[row.state] ?? '状态待核对'} · 在途 {row.inflight}{row.error && ' · ' + row.error}</p>)}
            {entry.kind === 'plugin' && <><p className="panel-hint">源位置{install ? '' : '（相对项目根目录）'}：{sourcePath}</p><button className="btn" onClick={() => void navigator.clipboard.writeText(sourcePath).then(() => notify('源位置已复制')).catch(cause => setError(formatBackendError(cause)))}>复制源位置</button></>}
            {plugin?.environment_required && <><p className="panel-hint">环境：{environmentLabels[plugin.environment?.state ?? 'NOT_PREPARED'] ?? '状态待核对'} {plugin.environment?.error}</p>
              <button className="btn" disabled={!!busy || state.enabled || plugin.environment?.state === 'PREPARING'} onClick={() => void perform(() => prepareCapability(entry.id), '准备执行环境')}>准备执行环境</button></>}
          </>}
          {tab === '设置' && <ContributionSettings identity={entry.id} {...props}/>}
          {tab === '文件' && skill && (skill.missing || skill.active?.kind === 'python' ? <p className="panel-hint">源目录缺失或属于旧式 Python 兼容包，本次编辑器只读。</p>
            : <SkillSourceEditor workspaceId={workspaceId} name={entry.id} onSaved={() => {void refresh();}}/>)}
          {tab === '版本' && (skill ? <SkillVersions row={skill} workspaceId={workspaceId} busy={!!busy} onAction={skillAction}/>
            : <PluginVersions row={install} busy={!!busy} perform={action => void perform(action)}/>)}
        </div>}
      </div>
    </Collapse>
  </article>;
}
