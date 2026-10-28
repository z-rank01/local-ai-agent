import {useCallback, useEffect, useRef, useState} from 'react';
import {fetchSkillSourceFiles, fetchSkillSourceFile, saveSkillSourceFile, type SkillSourceFile, type SkillSourceText} from '../../api';
import {formatBackendError} from '../../lib/format';
import {registerCapabilityLeave} from '../../lib/capabilityLeave';

export function SkillSourceEditor({workspaceId, name, onSaved}: {workspaceId: string; name: string; onSaved: () => void}) {
  const [files, setFiles] = useState<SkillSourceFile[]>([]);
  const [file, setFile] = useState<SkillSourceText>();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [leaving, setLeaving] = useState(false);
  const resolver = useRef<((result: boolean) => void)>();
  const editor = useRef<HTMLTextAreaElement>(null);
  const draftDirty = file?.writable && text !== file.content;
  const settle = useCallback((result: boolean) => {
    resolver.current?.(result); resolver.current = undefined; setLeaving(false);
  }, []);
  const save = useCallback(async (): Promise<boolean> => {
    if (!file?.writable || !file.sha256) return false;
    setBusy(true); setError(''); setNote('');
    try {
      const next = await saveSkillSourceFile(workspaceId, name, file.path, text, file.sha256);
      setFile(next); setText(next.content ?? ''); onSaved();
      setNote(next.activation === 'pending' ? '已保存并校验；新增权限或依赖待处理，请到版本中检查。' : '已保存并校验；下一次运行使用激活版本，在途运行保持原版本。');
      return true;
    } catch (cause) {setError(formatBackendError(cause)); return false;}
    finally {setBusy(false);}
  }, [file, text, workspaceId, name, onSaved]);
  useEffect(() => {
    let active = true;
    void fetchSkillSourceFiles(workspaceId, name).then(result => {if (active) setFiles(result.files);})
      .catch(cause => {if (active) setError(formatBackendError(cause));});
    return () => {active = false; resolver.current?.(false);};
  }, [workspaceId, name]);
  useEffect(() => {
    if (!draftDirty) return;
    const unregister = registerCapabilityLeave(() => new Promise(resolve => {
      if (busy || resolver.current) {resolve(false); return;}
      resolver.current = resolve; setLeaving(true);
    }));
    const unload = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = '';};
    window.addEventListener('beforeunload', unload);
    return () => {unregister(); window.removeEventListener('beforeunload', unload);};
  }, [draftDirty, busy]);
  useEffect(() => {
    if (!leaving) return;
    const escape = (event: KeyboardEvent) => {if (event.key === 'Escape') {event.preventDefault(); event.stopImmediatePropagation(); settle(false); editor.current?.focus();}};
    window.addEventListener('keydown', escape, true);
    return () => window.removeEventListener('keydown', escape, true);
  }, [leaving, settle]);
  async function select(path: string, discard = false) {
    if (draftDirty && !discard) {
      const {confirmCapabilityLeave} = await import('../../lib/capabilityLeave');
      if (!await confirmCapabilityLeave()) return;
    }
    setBusy(true); setError(''); setNote('');
    try {const value = await fetchSkillSourceFile(workspaceId, name, path); setFile(value); setText(value.content ?? '');}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  return <div className="cap-source">
    <p className="panel-hint">编辑源文件，不执行脚本；保存前核验包结构。仅支持现有 UTF-8 文本，最多 256 KiB。</p>
    <label className="field">源文件<select className="input" value={file?.path ?? ''} disabled={busy} onChange={event => void select(event.target.value)}>
      <option value="" disabled>选择文件…</option>{files.map(row => <option key={row.path} value={row.path}>{row.path}{row.writable ? '' : '（只读）'}</option>)}
    </select></label>
    {file && <>
      <p className="panel-hint">{file.path} · {file.size} 字节{draftDirty ? ' · 有未保存修改' : ''}{file.reason ? ' · ' + file.reason : ''}</p>
      {file.content !== null && <textarea ref={editor} className="input cap-editor" aria-label="Skill 源文件内容" spellCheck={false}
        value={text} readOnly={!file.writable} disabled={busy} onChange={event => setText(event.target.value)}/>}
      <div className="cap-actions"><button className="btn" disabled={!draftDirty || busy} onClick={() => void save()}>{busy ? '处理中…' : '保存并校验'}</button>
        <button className="btn btn-quiet" disabled={busy} onClick={() => void select(file.path)}>重新加载</button>
      </div>
    </>}
    {error && <p role="alert" className="run-error">{error}。草稿保留，不会自动覆盖或重发。</p>}
    {note && <p role="status" className="panel-hint">{note}</p>}
    {leaving && <div className="cap-leave" role="alertdialog" aria-label="处理未保存修改" aria-modal="false">
      <p>有未保存的修改，离开前如何处理？</p><div className="cap-actions">
        <button autoFocus className="btn" disabled={busy} onClick={() => void save().then(ok => {settle(ok);})}>保存并离开</button>
        <button className="btn" disabled={busy} onClick={() => {setText(file?.content ?? ''); settle(true);}}>放弃修改</button>
        <button className="btn btn-quiet" onClick={() => {settle(false); editor.current?.focus();}}>继续编辑</button>
      </div>
    </div>}
  </div>;
}
