import {useState} from 'react';
import {activateWorkspace, removeWorkspace, saveWorkspace} from '../api';
import {askConfirm, askForm} from '../lib/confirm';
import {notify} from '../lib/notify';
import type {Workspace} from '../types';
import {Dropdown} from '../ui/Dropdown';

export function WorkspaceManager({workspace, workspaces, busy, onSelect, onRefresh, onOpenConversation}: {
  workspace?: Workspace; workspaces: Workspace[]; busy: boolean;
  onSelect: (id: string) => void; onRefresh: () => Promise<void>; onOpenConversation?: (id: string) => void | Promise<void>;
}): JSX.Element {
  const [saving, setSaving] = useState(false);
  const run = async (action: () => Promise<unknown>) => {
    if (saving || busy) return;
    setSaving(true);
    try { await action(); await onRefresh(); } catch (err) { notify(err instanceof Error ? err.message : String(err), 'error'); }
    finally { setSaving(false); }
  };
  const edit = async (create: boolean) => {
    const values = await askForm({title: create ? '新建工作区' : '编辑工作区', fields: [
      {key: 'name', label: '工作区名称', value: create ? '' : workspace?.name, required: true},
    ], okLabel: '保存'});
    if (!values) return;
    await run(async () => { const next = await saveWorkspace(values, create ? undefined : workspace?.id); if (create) onSelect(next.id); });
  };
  const addRoot = async () => {
    if (!workspace) return;
    const values = await askForm({title: '添加本机目录', fields: [
      {key: 'host_path', label: '目录绝对路径', placeholder: 'E:\\DATA', required: true},
      {key: 'name', label: '显示名称', placeholder: '资料'},
    ], okLabel: '添加'});
    if (!values) return;
    if (workspace.cloud_read && !await askConfirm({title: '扩展云端授权？', body: '新增目录将沿用此工作区现有的云端读取及写入授权。', okLabel: '确认添加'})) return;
    await run(() => saveWorkspace({roots: [...workspace.roots, values], confirm_cloud_expansion: true}, workspace.id));
  };
  const removeRoot = async (id: string) => {
    if (!workspace || !await askConfirm({title: '移除目录关联？', body: '原目录和文件会保留。历史附件可能暂时无法访问。', okLabel: '移除'})) return;
    await run(() => saveWorkspace({roots: workspace.roots.filter(r => r.id !== id)}, workspace.id));
  };
  const active = workspace?.active_conversation_id;
  return <section className="workspace-manager">
    <Dropdown value={workspace?.id ?? 'default'} options={workspaces.map(w => ({value: w.id, label: w.name}))}
      onChange={onSelect} disabled={busy || saving} ariaLabel="当前工作区" />
    <div className="workspace-manager-actions">
      <button className="btn btn-quiet btn-sm" disabled={busy || saving} onClick={() => void edit(true)}>新建工作区</button>
      <button className="btn btn-quiet btn-sm" disabled={busy || saving || !workspace} onClick={() => void edit(false)}>重命名</button>
      <button className="btn btn-secondary btn-sm" disabled={busy || saving || !workspace} onClick={() => void addRoot()}>添加目录</button>
      {workspace?.id !== 'default' && <button className="btn btn-quiet btn-sm" disabled={busy || saving} onClick={async () => {
        if (workspace && await askConfirm({title: '删除工作区？', body: '需要先删除所属会话。此操作不删除本机文件。', danger: true})) {
          await run(async () => { await removeWorkspace(workspace.id); onSelect('default'); });
        }
      }}>删除工作区</button>}
    </div>
    {workspace && <button className="btn btn-quiet btn-sm" disabled={busy || saving} onClick={() => void run(() => saveWorkspace({primary_root_id: null}, workspace.id))}>
      {workspace.primary_root_id === null ? '主要目录：托管文件' : '使用托管文件作为主要目录'}
    </button>}
    {workspace?.roots.map(r => <div className="workspace-root" key={r.id}>
      <span><b>{r.name}</b><small>{r.host_path}</small>{!r.available && <span role="status">目录不可用</span>}</span>
      <button className="btn btn-quiet btn-sm" disabled={busy || saving} onClick={() => void run(() => saveWorkspace({primary_root_id: r.id}, workspace.id))}>
        {workspace.primary_root_id === r.id ? '主要目录' : '设为主要'}
      </button>
      <button className="btn btn-quiet btn-sm" disabled={busy || saving} aria-label={`移除目录 ${r.name}`} onClick={() => void removeRoot(r.id)}>移除</button>
    </div>)}
    {active && <p className="panel-hint" role="status">此工作区或共享目录正在执行任务。{!active.startsWith('__') && <button className="btn btn-quiet btn-sm" onClick={() => void onOpenConversation?.(active)}>查看执行会话</button>}</p>}
    {workspace?.last_error && <p className="panel-hint" role="status">工具配置尚未生效，请确认 Docker 已启动，再检查并应用目录配置。</p>}
    {workspace?.pending && <p className="panel-hint" role="status">目录配置待生效，原有效配置继续保留。</p>}
    {workspace && <button className="btn btn-quiet btn-sm" disabled={busy || saving} onClick={() => void run(() => activateWorkspace(workspace.id))}>检查并应用目录配置</button>}
  </section>;
}
