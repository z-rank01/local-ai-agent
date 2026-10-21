import {useCallback, useEffect, useState} from 'react';
import {changeManagedSkill, fetchManagedSkills, importManagedSkill, type ManagedSkill} from '../api';
import {formatBackendError} from '../lib/format';
import {askConfirm} from '../lib/confirm';
import {SkillDiff} from './SkillDiff';

export function SkillManager({workspaceId}: {workspaceId: string}): JSX.Element {
  const [rows, setRows] = useState<ManagedSkill[]>([]);
  const [invalid, setInvalid] = useState<{name: string; error: string}[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState('');
  const refresh = useCallback(async () => {
    const result = await fetchManagedSkills(workspaceId);
    setRows(result.skills); setInvalid(result.invalid); setError('');
  }, [workspaceId]);
  useEffect(() => {
    let active = true;
    setRows([]); setInvalid([]); setError('');
    void fetchManagedSkills(workspaceId).then(result => {
      if (active) {setRows(result.skills); setInvalid(result.invalid);}
    }).catch(cause => {if (active) setError(formatBackendError(cause));});
    return () => {active = false;};
  }, [workspaceId]);
  async function action(row: ManagedSkill, name: string, revision?: string) {
    if (name === 'uninstall' && !await askConfirm({title: '从能力目录移除此技能？', body: '原文件和历史版本保留，之后可以重新启用。'})) return;
    setBusy(true);
    try {await changeManagedSkill(workspaceId, row.id, name, revision); await refresh();}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  async function check() {
    setBusy(true);
    try {await refresh();} catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  async function importPackage() {
    setBusy(true);
    try {await importManagedSkill(workspaceId, path); setPath(''); await refresh();}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  return <details>
    <summary>工作区 Skill 与版本</summary>
    <p className="panel-hint">将标准技能包放入本工作区的 skills 目录即可发现。已运行的聊天固定使用原版本；新增权限或依赖需检查后激活。</p>
    <button className="btn" type="button" disabled={busy} onClick={() => void check()}>检查目录与更新</button>
    <label className="field"><span className="field-label">导入工作区中的技能目录</span>
      <input value={path} onChange={event => setPath(event.target.value)} placeholder="/workspace/下载的技能包" />
    </label>
    <button className="btn" type="button" disabled={busy || !path.trim()} onClick={() => void importPackage()}>导入目录</button>
    {error ? <p role="alert" className="panel-hint">{error}</p> : null}
    {invalid.map(row => <p role="status" key={row.name} className="panel-hint">{row.name}：{row.error}</p>)}
    {rows.map(row => <details key={row.id}>
      <summary>{row.id} · {row.missing ? '源目录缺失' : row.uninstalled ? '已移出目录' : !row.enabled ? '已停用' : row.active ? '已启用' : '待激活'}</summary>
      <p className="panel-hint">{(row.pending ?? row.active)?.description}</p>
      <p className="panel-hint">当前版本：{row.active?.version || '未标注'} · {row.active?.revision.slice(0, 12) ?? '无'}</p>
      {row.blockers.map(message => <p className="panel-hint" key={message}>{message}</p>)}
      {row.pending ? <div className="field">
        <span className="field-label">待激活版本：{row.pending.revision.slice(0, 12)}</span>
        <SkillDiff key={row.pending.revision} workspaceId={workspaceId} name={row.id} revision={row.pending.revision} />
        <span className="panel-hint">权限：{row.pending.permissions.join('、') || '无额外声明'}；依赖：{row.pending.dependencies.join('、') || '无'}</span>
        <button className="btn" disabled={busy || row.blockers.length > 0} onClick={() => void action(row, 'activate', row.pending!.revision)}>激活此版本</button>
      </div> : null}
      <div className="row-lead">
        <button className="btn" disabled={busy || Boolean(row.missing)} onClick={() => void action(row, row.enabled && !row.uninstalled ? 'disable' : 'enable')}>
          {row.enabled && !row.uninstalled ? '停用' : '启用'}</button>
        <button className="btn" disabled={busy || Boolean(row.uninstalled)} onClick={() => void action(row, 'uninstall')}>移出能力目录</button>
      </div>
      {row.history.length ? <details><summary>历史版本与回退</summary>{row.history.map(version => <p key={version.revision}>
        <span className="panel-hint">{version.version || '未标注'} · {version.revision.slice(0, 12)} </span>
        <button className="btn" disabled={busy || Boolean(row.missing)} onClick={() => void action(row, 'rollback', version.revision)}>使用此版本</button>
      </p>)}</details> : null}
    </details>)}
  </details>;
}
