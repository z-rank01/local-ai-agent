import type {ManagedSkill} from '../../api';
import {SkillDiff} from '../SkillDiff';

export function SkillVersions({row, workspaceId, busy, onAction}: {row: ManagedSkill; workspaceId: string; busy: boolean;
  onAction: (action: string, revision?: string) => void}) {
  const versions = [row.pending, ...row.history].filter((row): row is NonNullable<typeof row> => Boolean(row));
  return <>
    <p className="panel-hint">已激活：{row.active?.version || '未标注'} · {row.active?.revision.slice(0, 12) ?? '无'}。版本切换只影响后续运行。</p>
    {row.candidate_error && <p role="alert" className="run-error">源包校验未通过：{row.candidate_error}；已激活快照保留。</p>}
    {row.blockers.map(message => <p key={message} role="status" className="panel-hint">{message}</p>)}
    {!versions.length && <p className="panel-hint">暂无待激活或历史版本。</p>}
    {versions.map(version => <div className="cap-version" key={version.revision}>
      <strong>{version.revision === row.pending?.revision ? '待激活' : '历史版本'} · {version.version || '未标注'}</strong>
      <p className="panel-hint">{version.revision.slice(0, 16)}<br/>权限：{version.permissions.join('、') || '无额外声明'}；依赖：{version.dependencies.join('、') || '无'}</p>
      <SkillDiff workspaceId={workspaceId} name={row.id} revision={version.revision}/>
      <button className="btn" disabled={busy || Boolean(row.missing) || Boolean(row.blockers.length)} onClick={() => onAction(version.revision === row.pending?.revision ? 'activate' : 'rollback', version.revision)}>
        {version.revision === row.pending?.revision ? '激活此版本' : '回退到此版本'}</button>
    </div>)}
  </>;
}
