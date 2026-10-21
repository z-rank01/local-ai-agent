import {useState} from 'react';
import {fetchSkillDiff, type SkillDiff as Diff} from '../api';
import {formatBackendError} from '../lib/format';

export function SkillDiff({workspaceId, name, revision}: {workspaceId: string; name: string; revision: string}) {
  const [result, setResult] = useState<Diff>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setError('');
    try {setResult(await fetchSkillDiff(workspaceId, name, revision));}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  return <div>
    <button className="btn" disabled={busy} onClick={() => void load()}>查看与当前版本的差异</button>
    {error ? <p role="alert">{error}</p> : null}
    {result?.files.length === 0 ? <p>文件内容相同</p> : null}
    {result?.files.map(file => <details key={file.path}>
      <summary>{file.path} · {({added:'新增', removed:'删除', modified:'修改'} as Record<string, string>)[file.change]}</summary>
      <p className="panel-hint">{file.before_bytes} → {file.after_bytes} 字节</p>
      {file.diff ? <pre style={{whiteSpace:'pre-wrap', overflowWrap:'anywhere'}}>{file.diff}</pre> : null}
      {file.truncated || file.preview ? <p className="panel-hint">{file.preview === 'binary' ? '二进制文件不显示文本差异' : '预览已限制，请核对版本中的完整文件'}</p> : null}
    </details>)}
  </div>;
}
