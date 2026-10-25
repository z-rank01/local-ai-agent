import {useEffect, useState} from 'react';
import {fetchPackages, importPackage, changePackage, fetchPackageDiff, type PackageDiff, type InstalledPackage, type PackageRevision} from '../extensions/packages';
import {formatBackendError} from '../lib/format';

export function PluginPackages(): JSX.Element {
  const [rows, setRows] = useState<InstalledPackage[]>([]);
  const [source, setSource] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [trusted, setTrusted] = useState<Record<string, boolean>>({});
  const [diffs, setDiffs] = useState<Record<string, PackageDiff>>({});
  async function refresh() {setRows((await fetchPackages()).packages);}
  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try {await action(); await refresh(); setTrusted({});}
    catch (cause) {setError(formatBackendError(cause));}
    finally {setBusy(false);}
  }
  useEffect(() => {void refresh().catch(cause => setError(formatBackendError(cause)));}, []);
  function version(row: InstalledPackage, entry: PackageRevision, rollback: boolean) {
    const key = row.id + ':' + entry.revision;
    return <div className="field" key={key}>
      <span className="field-label">{entry.manifest.title} · {entry.manifest.version}</span>
      <span className="panel-hint">版本 {entry.revision.slice(0, 16)} · 来源 {entry.source}</span>
      <span className="panel-hint">权限：{entry.manifest.permissions.join('、') || '无声明'}；依赖：{entry.manifest.required.join('、') || '无'}</span>
      <button className="btn" disabled={busy} onClick={() => void perform(async () => {
        const diff = await fetchPackageDiff(row.id, entry.revision); setDiffs(previous => ({...previous, [key]:diff}));
      })}>查看与已安装版本的差异</button>
      {diffs[key]?.files.map(file => <div className="field" key={file.path}>
        <span className="panel-hint">{file.path} · {({added:'新增', removed:'删除', changed:'修改'} as Record<string,string>)[file.change]}</span>
        {file.diff ? <pre>{file.diff}</pre> : null}
        {file.truncated || file.binary ? <span className="panel-hint">仅显示有限预览，请核对源包内容。</span> : null}
      </div>)}
      <label><input type="checkbox" checked={!!trusted[key]} onChange={event => setTrusted({...trusted, [key]:event.target.checked})}/>
        我信任此版本的代码及上述权限</label>
      <button className="btn" disabled={busy || !trusted[key]} onClick={() => void perform(() =>
        changePackage(row.id, rollback ? 'rollback' : 'activate', entry.revision, true))}>
        {rollback ? '回退到此版本' : '安装此版本'}（重启生效）</button>
    </div>;
  }
  return <section aria-label="本地插件安装">
    <p className="panel-hint">导入只保存内容。确认版本后，正常退出并从启动.bat 重新打开，由启动器构建；安装完成后再准备所需环境并启用。</p>
    <form onSubmit={event => {event.preventDefault(); void perform(() => importPackage(source));}}>
      <label className="field">插件目录或 ZIP 的本机完整路径
        <input value={source} onChange={event => setSource(event.target.value)} disabled={busy}/></label>
      <button className="btn" disabled={busy || !source.trim()}>导入／检查更新</button>
      <button className="btn" type="button" disabled={busy} onClick={() => void perform(refresh)}>刷新</button>
    </form>
    {error ? <p className="panel-ok" role="alert">{error}</p> : null}
    {!rows.length ? <p className="panel-hint">尚未导入本地插件。</p> : null}
    {rows.map(row => <div className="field" key={row.id}>
      <span className="field-label">{row.active?.manifest.title ?? row.pending?.manifest.title ?? row.id}</span>
      <span className="panel-hint">{({PENDING_RESTART:'等待正常退出后重启', BUILD_REQUIRED:'等待启动器完成构建',
        INSTALLED:'已安装，可在能力清单启用', REMOVED:'已卸载，历史版本和成果保留', DEPLOY_FAILED:'部署失败',
        BUILD_FAILED:'构建失败，已恢复先前安装内容'} as Record<string,string>)[row.deployment ?? ''] ?? '已导入，等待确认版本'}</span>
      {row.error ? <p role="alert">{row.error}</p> : null}
      {row.pending ? version(row, row.pending, false) : null}
      {row.active ? <span className="panel-hint">已安装版本：{row.active.manifest.version} · {row.active.revision.slice(0,16)}</span> : null}
      {row.active ? <button className="btn" disabled={busy} onClick={() => void perform(() => importPackage(row.active!.source))}>从原来源检查更新</button> : null}
      {row.history.map(entry => version(row, entry, true))}
      {!row.uninstalled ? <button className="btn" disabled={busy} onClick={() => void perform(() => changePackage(row.id, 'uninstall'))}>卸载插件（重启生效）</button> : null}
    </div>)}
  </section>;
}
