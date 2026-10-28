import {useState} from 'react';
import {importManagedSkill} from '../../api';
import {importPackage} from '../../extensions/packages';
import {formatBackendError} from '../../lib/format';

export function CapabilityImport({workspaceId, kind, onDone, onCancel}: {
  workspaceId: string; kind: 'plugin' | 'skill'; onDone: (id: string) => void; onCancel: () => void;
}) {
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit() {
    setBusy(true); setError('');
    try {
      const result = kind === 'skill' ? await importManagedSkill(workspaceId, path) : await importPackage(path) as {id?: string; name?: string};
      onDone(('name' in result ? result.name : result.id) ?? '');
    } catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  return <form className="cap-import" onSubmit={event => {event.preventDefault(); void submit();}}>
    <label className="field"><span className="field-label">{kind === 'skill' ? '导入 Skill：已挂载目录中的路径' : '导入 Plugin：本机目录或 ZIP 完整路径'}</span>
      <input autoFocus className="input" value={path} disabled={busy} onChange={event => setPath(event.target.value)}
        placeholder={kind === 'skill' ? '/workspace/下载的技能包' : 'C:\\能力包\\example.zip'}/>
    </label>
    <p className="panel-hint">{kind === 'skill' ? '只导入内容，不执行代码；新增权限与依赖需检查。' : '导入不执行代码；确认具体版本后，正常退出再从启动.bat 打开以部署构建。'}</p>
    {error && <p role="alert" className="run-error">{error}</p>}
    <div className="cap-actions"><button className="btn" disabled={busy || !path.trim()}>{busy ? '导入中…' : '导入'}</button>
      <button type="button" className="btn btn-quiet" disabled={busy} onClick={onCancel}>取消</button></div>
  </form>;
}
