import {useEffect, useState} from 'react';
import {requestJson} from '../../extensions/http';

type Kind = 'plugin' | 'skill';
type Grant = {identity: string; kind: Kind; public_key: string; permissions: string[]; actions: string[]; approved_revision?: string};
const operations = {status:'查看', import:'导入', install:'安装', upgrade:'升级', enable:'启用', disable:'停用', uninstall:'卸载'};

export function SourceGrants({workspaceId}: {workspaceId: string}) {
  const endpoint = '/api/plugin-packages/grants/' + encodeURIComponent(workspaceId);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [kind, setKind] = useState<Kind>('skill');
  const [name, setName] = useState('');
  const [publicKey, setPublicKey] = useState('');
  const [permissions, setPermissions] = useState('');
  const [revision, setRevision] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true;
    requestJson<{grants: Grant[]}>(endpoint).then(v => {if (active) setGrants(v.grants);})
      .catch(e => {if (active) setMessage(String(e));});
    return () => {active = false;};
  }, [endpoint]);
  async function save(grant: Grant) {
    setBusy(true);
    try {
      await requestJson(endpoint + '/' + encodeURIComponent(grant.identity), {method:'PUT',
        headers:{'content-type':'application/json'}, body: JSON.stringify({...grant, host_scope:grant.kind === 'plugin'})});
      const result = await requestJson<{grants: Grant[]}>(endpoint);
      setGrants(result.grants);
      setMessage(grant.actions.length ? '授权已保存' : '授权已撤销');
    } catch (error) {setMessage(String(error));} finally {setBusy(false);}
  }
  return <section aria-label="受信来源管理">
    <p>允许本工作区的 AI 管理指定来源的能力。插件变更适用于整个宿主。</p>
    {grants.map(g => <p key={g.kind + g.identity}>{g.kind} · {g.identity} · {g.actions.join(' / ')} <button
      className="btn" disabled={busy} onClick={() => void save({...g, actions:[]})}>撤销</button></p>)}
    <form onSubmit={e => {e.preventDefault(); void save({identity:name, kind, public_key:publicKey,
      permissions:permissions.trim().split(/\s+/).filter(Boolean), approved_revision:revision || undefined, actions});}}>
      <label>管理范围 <select value={kind} onChange={e => setKind(e.target.value as Kind)}>
        <option value="skill">工作区技能</option><option value="plugin">宿主插件</option></select></label>
      <label>能力 ID <input required value={name} onChange={e => setName(e.target.value)}/></label>
      <label>发布者 Ed25519 公钥 <input required pattern="[0-9a-f]{64}" value={publicKey} onChange={e => setPublicKey(e.target.value)}/></label>
      <label>允许的权限（空格分隔） <input value={permissions} onChange={e => setPermissions(e.target.value)}/></label>
      <label>批准增加权限的版本（可选 SHA-256） <input pattern="[a-f0-9]{64}" value={revision} onChange={e => setRevision(e.target.value)}/></label>
      <fieldset><legend>允许的操作</legend>{Object.entries(operations).map(([op, label]) =>
        <label key={op}><input type="checkbox" checked={actions.includes(op)}
          onChange={e => setActions(e.target.checked ? [...actions, op] : actions.filter(a => a !== op))}/>{label}</label>)}</fieldset>
      <button className="btn" disabled={busy || !actions.length}>保存来源授权</button>
    </form><p role="status">{message}</p>
  </section>;
}
