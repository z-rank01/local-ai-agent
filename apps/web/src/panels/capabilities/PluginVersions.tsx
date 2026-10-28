import {useState} from 'react';
import {changePackage, fetchPackageDiff, importPackage, type InstalledPackage, type PackageDiff} from '../../extensions/packages';

export function PluginVersions({row, busy, perform}: {row?: InstalledPackage; busy: boolean; perform: (action: () => Promise<unknown>) => void}) {
  const [trusted, setTrusted] = useState<Record<string, boolean>>({});
  const [diffs, setDiffs] = useState<Record<string, PackageDiff>>({});
  if (!row) return <p className="panel-hint">内置 Plugin 随项目部署更新；此处不提供安装副本回退与卸载。</p>;
  return <>
    <p className="panel-hint">已安装：{row.active?.manifest.version ?? '无'} · {row.active?.revision.slice(0, 12) ?? '—'}。更新和回退需信任具体版本，正常退出后从启动.bat 重新打开。</p>
    {row.active && <button className="btn" disabled={busy} onClick={() => perform(() => importPackage(row.active!.source))}>从原来源检查更新</button>}
    {[row.pending, ...row.history.filter(item => item.revision !== row.active?.revision && item.revision !== row.pending?.revision)]
      .filter((item): item is NonNullable<typeof item> => Boolean(item)).map(item => <div className="cap-version" key={item.revision}>
        <strong>{item.revision === row.pending?.revision ? '候选' : '历史'} · {item.manifest.version}</strong>
        <p className="panel-hint">{item.revision.slice(0, 16)}<br/>来源：{item.source}<br/>权限：{item.manifest.permissions.join('、') || '无'}；依赖：{item.manifest.required.join('、') || '无'}</p>
        <button className="btn" disabled={busy} onClick={() => perform(async () => {const diff = await fetchPackageDiff(row.id, item.revision); setDiffs(previous => ({...previous, [item.revision]: diff}));})}>查看与已安装版本的差异</button>
        {diffs[item.revision]?.files.map(file => <details key={file.path}><summary>{file.path} · {file.change}</summary>
          {file.diff && <pre className="cap-code">{file.diff}</pre>}{(file.truncated || file.binary) && <p className="panel-hint">有限预览，请核对完整源包。</p>}</details>)}
        <label className="cap-check"><input type="checkbox" checked={!!trusted[item.revision]} disabled={busy} onChange={event => setTrusted(previous => ({...previous, [item.revision]: event.target.checked}))}/>我信任此版本的代码及上述权限</label>
        <button className="btn" disabled={busy || !trusted[item.revision]} onClick={() => perform(() => changePackage(row.id, item.revision === row.pending?.revision ? 'activate' : 'rollback', item.revision, true))}>
          {item.revision === row.pending?.revision ? '安装此版本' : '回退到此版本'}（重启生效）</button>
      </div>)}
  </>;
}
