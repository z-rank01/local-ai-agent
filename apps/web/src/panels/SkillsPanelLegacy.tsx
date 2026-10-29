import {useEffect, useRef, useState} from 'react';
import {Icon} from '../ui/Icon';
import {Popover} from '../ui/Popover';
import type {CapabilityPanelProps} from '../extensions/contracts';
import {confirmCapabilityLeave} from '../lib/capabilityLeave';
import {useCapabilityRegistry} from '../lib/capabilityRegistry';
import type {SkillsPanelProps} from './capabilityPanelTypes';
import {inventory} from './capabilities/inventory';
import {CapabilityEntry} from './capabilities/CapabilityEntry';
import {CapabilityImport} from './capabilities/CapabilityImport';

export function SkillsPanelLegacy({
  workspaceId,
  selectedCapabilityIds: _selectedCapabilityIds,
  onCapabilitySelected: _onCapabilitySelected,
  ...props
}: SkillsPanelProps): JSX.Element {
  return <AbilityManager workspaceId={workspaceId} {...props}/>;
}

function AbilityManager({workspaceId, ...props}: CapabilityPanelProps & {workspaceId: string}) {
  const data = useCapabilityRegistry();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [history, setHistory] = useState(false);
  const [expanded, setExpanded] = useState('');
  const [menu, setMenu] = useState(false);
  const [importKind, setImportKind] = useState<'plugin' | 'skill'>();
  const importAnchor = useRef<HTMLButtonElement>(null);
  useEffect(() => {setImportKind(undefined); setExpanded(previous => previous.startsWith('skill:') ? '' : previous);}, [workspaceId]);
  const rows = inventory(data.plugins, data.packages, data.skills);
  const matched = rows.filter(row => (history || !row.removed) &&
    (row.title + ' ' + row.id + ' ' + row.description).toLowerCase().includes(query.trim().toLowerCase()));
  const visible = matched.filter(row => filter === 'all' || row.kind === filter);
  async function change(action: () => void) {if (await confirmCapabilityLeave()) action();}
  async function imported(id: string) {
    setImportKind(undefined); setQuery(''); setFilter('all'); setHistory(false);
    await data.refresh(); if (id) setExpanded(importKind + ':' + id);
  }
  return <div className="cap-manager">
    <div className="cap-toolbar">
      <label className="cap-search"><Icon name="i-search" className="ico-sm"/><input className="input input-sm" type="search" aria-label="搜索全部能力" placeholder="搜索名称、ID 或用途…"
        value={query} onChange={event => {const value = event.target.value; void change(() => setQuery(value));}}/></label>
      <button ref={importAnchor} className="btn" aria-label="导入能力" aria-expanded={menu} onClick={() => setMenu(!menu)}>导入</button>
      <button className="btn btn-quiet" disabled={data.loading} onClick={() => void data.refresh()}>{data.loading ? '加载中…' : '刷新'}</button>
    </div>
    <Popover anchor={importAnchor} open={menu} onClose={() => setMenu(false)}><div className="cap-menu" role="menu" aria-label="选择导入类型">
      {(['skill', 'plugin'] as const).map(kind => <button key={kind} role="menuitem" onClick={() => void change(() => {setMenu(false); setImportKind(kind);})}>导入 {kind === 'skill' ? 'Skill' : 'Plugin'}</button>)}
    </div></Popover>
    <div className="cap-filters" role="group" aria-label="能力类型">{[['all', '全部'], ['plugin', 'Plugin'], ['skill', 'Skill']].map(([kind, label]) =>
      <button key={kind} aria-pressed={filter === kind} onClick={() => void change(() => setFilter(kind))}>{label} <span>{matched.filter(row => kind === 'all' || row.kind === kind).length}</span></button>)}</div>
    <label className="cap-check"><input type="checkbox" checked={history} onChange={event => {const checked = event.target.checked; void change(() => setHistory(checked));}}/>显示已移除的历史记录</label>
    {importKind && <CapabilityImport workspaceId={workspaceId} kind={importKind} onDone={id => void imported(id)} onCancel={() => setImportKind(undefined)}/>}
    {data.error && <p className="run-error" role="alert">{data.error}</p>}
    {data.invalid.map(row => <p className="run-error" key={row.name} role="status">{row.name}：{row.error}</p>)}
    {!data.loading && !visible.length && <div className="ws-empty"><p>没有匹配的能力。</p><button className="btn" onClick={() => {setQuery(''); setFilter('all');}}>清除筛选</button></div>}
    <div className="cap-list">{visible.map(row => <CapabilityEntry key={row.kind === 'skill' ? workspaceId + ':' + row.key : row.key} entry={row} workspaceId={workspaceId} open={expanded === row.key}
      onOpen={() => void change(() => setExpanded(expanded === row.key ? '' : row.key))} refresh={() => data.refresh()} {...props}/>)}</div>
    <p className="panel-hint">Plugin 为本机能力，Skill 属于当前工作区。启停等待服务确认；Skill 版本影响后续运行，Plugin 安装、回退与卸载按卡片提示重启生效。</p>
  </div>;
}
