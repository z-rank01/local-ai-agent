import {useRef, useState} from 'react';
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
import {entryState, type CapabilityEntry as Entry} from './inventory';
import {SkillSourceEditor} from './SkillSourceEditor';
import {SkillVersions} from './SkillVersions';
import {PluginVersions} from './PluginVersions';

export function CapabilityEntry({entry, workspaceId, open, onOpen, refresh, ...props}: CapabilityPanelProps & {
  entry: Entry; workspaceId: string; open: boolean; onOpen: () => void; refresh: () => Promise<void>;
}) {
  const [tab, setTab] = useState('概览');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [menu, setMenu] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const state = entryState(entry);
  const {plugin, skill, install} = entry;
  const source = entry.kind === 'skill' ? skill?.active?.kind === 'python' ? '旧式 Python 兼容包' : '工作区目录包' : install ? '用户导入' : '内置';
  const tabs = ['概览', ...(entry.kind === 'plugin' && hasContribution(entry.id) ? ['设置'] : []),
    ...(skill ? ['文件'] : []), '版本'];
  async function selectTab(next: string) {
    if (await confirmCapabilityLeave()) setTab(next);
  }
  async function perform(action: () => Promise<unknown>, label = '处理中') {
    if (!await confirmCapabilityLeave()) return;
    setBusy(label); setError(''); setMenu(false);
    try {await action(); await refresh(); props.onChanged?.(); notify('操作已确认；生效条件见卡片状态');}
    catch (cause) {setError(formatBackendError(cause)); await refresh();}
    finally {setBusy('');}
  }
  function skillAction(action: string, revision?: string) {
    void perform(() => changeManagedSkill(workspaceId, entry.id, action, revision));
  }
  async function toggle(next: boolean) {
    if (!next && entry.id === 'finance' && !await askConfirm({title:'停用金融研究', body:'后台任务会在安全边界收尾；这不会删除报告或替你确认账户。', okLabel:'停用', danger:true})) return;
    void perform(() => entry.kind === 'plugin' ? setCapability(entry.id, next)
      : changeManagedSkill(workspaceId, entry.id, next ? 'enable' : 'disable'), next ? '正在启用' : '等待收尾');
  }
  async function remove() {
    const accepted = await askConfirm({title: skill ? '移出能力目录？' : '卸载安装副本？',
      body: skill ? '保留源文件、历史快照和成果，之后可从历史记录重新启用。' : '保留包快照及成果。正常退出后从启动.bat 重启才会移除安装副本。',
      okLabel: skill ? '移出目录' : '安排卸载', danger: true});
    if (accepted) void perform(() => skill ? changeManagedSkill(workspaceId, entry.id, 'uninstall') : changePackage(entry.id, 'uninstall'));
  }
  const canToggle = skill ? !skill.missing : Boolean(plugin?.actions?.toggle);
  const updating = install?.deployment === 'PENDING_RESTART' || install?.deployment === 'BUILD_REQUIRED';
  const failedUpdate = install?.deployment === 'BUILD_FAILED' || install?.deployment === 'DEPLOY_FAILED';
  return <article className="cap-entry" data-open={open ? 'true' : 'false'}>
    <div className="cap-entry-head">
      <button ref={trigger} className="cap-entry-title" onClick={onOpen} aria-expanded={open} aria-label={`${open ? '收起' : '展开'}${entry.title}详情`}>
        <strong>{entry.title}</strong><span className="panel-hint">{entry.description}</span>
        <span className="cap-meta">{entry.kind === 'plugin' ? 'Plugin · 本机能力' : 'Skill · 当前工作区'} · {source}</span>
      </button>
      <div className="cap-entry-controls"><span className={`chip chip-${busy ? 'warn' : state.tone}`} role="status">{busy || state.label}</span>
        <Switch label={`启用${entry.title}`} checked={state.enabled} disabled={Boolean(busy) || !canToggle} onChange={next => void toggle(next)}/>
        <button ref={anchor} className="tb-btn" aria-label={`${entry.title}更多操作`} aria-expanded={menu} onClick={() => setMenu(!menu)}><Icon name="i-menu"/></button>
      </div>
    </div>
    {updating && <p className="panel-hint cap-inline">{install?.deployment === 'BUILD_REQUIRED' ? '等待启动器构建确认' : '已安排变更，正常退出后重启生效'}</p>}
    {!updating && !failedUpdate && (install?.pending || skill?.pending) && <p className="panel-hint cap-inline">有待审阅版本，请在“版本”中查看差异与生效条件。</p>}
    {failedUpdate && <p className="panel-hint cap-inline">{state.enabled ? '当前版本可用 · ' : ''}上次更新失败；候选版本与错误保留在详情中。</p>}
    {error && <p className="run-error cap-inline" role="alert">{error}</p>}
    <Popover anchor={anchor} open={menu} onClose={() => setMenu(false)}>
      <div role="menu" aria-label={`${entry.title}操作`} className="cap-menu">
        <button role="menuitem" disabled={!!busy} onClick={() => {setMenu(false); if (!open) onOpen(); else trigger.current?.focus();}}>查看详情</button>
        <button role="menuitem" disabled={!!busy} onClick={() => void perform(install?.active ? () => importPackage(install.active!.source) : refresh)}>{skill ? '检查目录与更新' : install?.active ? '从原来源检查更新' : '刷新状态'}</button>
        <button role="menuitem" disabled={!!busy || (!skill && !install)} onClick={() => {setMenu(false); void remove();}}>{skill ? '移出能力目录' : '卸载安装副本'}</button>
        {!skill && !install && <p className="panel-hint">内置插件随项目部署更新，可停用。</p>}
      </div>
    </Popover>
    <Collapse open={open} className="cap-body"><div className="cap-detail">
      <div className="cap-tabs" role="tablist" aria-label={`${entry.title}详情栏目`}>{tabs.map((label, index) => <button key={label} role="tab" aria-selected={tab === label} tabIndex={tab === label ? 0 : -1}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
          void selectTab(tabs[next]); (event.currentTarget.parentElement?.querySelectorAll('button')[next] as HTMLElement)?.focus();
        }} onClick={() => void selectTab(label)}>{label}</button>)}</div>
      {<div className="cap-tab-content" role="tabpanel" aria-label={tab}>
        {tab === '概览' && <>
          <p className="panel-hint">ID：{entry.id}<br/>版本：{plugin?.version ?? skill?.active?.version ?? install?.active?.manifest.version ?? '未标注'}</p>
          <p className="panel-hint">权限：{(plugin?.permissions ?? skill?.pending?.permissions ?? skill?.active?.permissions ?? []).join('、') || '无额外声明'}<br/>
            必需依赖：{(plugin?.required ?? skill?.active?.dependencies ?? []).join('、') || '无'}<br/>可选依赖：{plugin?.optional?.join('、') || '无'}</p>
          <p className="panel-hint">{skill ? '启停和版本选择影响后续运行；在途运行保持固定版本。移出目录保留原件和历史。' : plugin?.action_note ?? '本地包需审阅并确认版本，正常退出后部署构建。'}</p>
          {(plugin?.error || install?.error || skill?.candidate_error) && <p className="run-error">{plugin?.error || install?.error || skill?.candidate_error}</p>}
          {Object.entries(plugin?.lifecycle ?? {}).map(([owner, row]) => <p className="panel-hint" key={owner}>{owner} · {row.state} · 在途 {row.inflight}{row.error && ' · ' + row.error}</p>)}
          {install && <><p className="panel-hint">源包：{(install.active ?? install.pending)?.source}</p><button className="btn" onClick={() => void navigator.clipboard.writeText((install.active ?? install.pending)?.source ?? '').then(() => notify('源位置已复制')).catch(cause => setError(formatBackendError(cause)))}>复制源位置</button></>}
          {plugin?.environment_required && <><p className="panel-hint">环境：{plugin.environment?.state ?? 'NOT_PREPARED'} {plugin.environment?.error}</p>
            <button className="btn" disabled={!!busy || state.enabled || plugin.environment?.state === 'PREPARING'} onClick={() => void perform(() => prepareCapability(entry.id), '准备执行环境')}>准备执行环境</button></>}
        </>}
        {tab === '设置' && <ContributionSettings identity={entry.id} {...props}/>}
        {tab === '文件' && skill && (skill.missing || skill.active?.kind === 'python' ? <p className="panel-hint">源目录缺失或属于旧式 Python 兼容包，本次编辑器只读。</p>
          : <SkillSourceEditor workspaceId={workspaceId} name={entry.id} onSaved={() => {void refresh();}}/>)}
        {tab === '版本' && (skill ? <SkillVersions row={skill} workspaceId={workspaceId} busy={!!busy} onAction={skillAction}/>
          : <PluginVersions row={install} busy={!!busy} perform={action => void perform(action)}/>)}
      </div>}
    </div></Collapse>
  </article>;
}
