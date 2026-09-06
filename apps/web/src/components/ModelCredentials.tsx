import {useState} from 'react';
import {DEFAULT_BASE_URL} from '../api';
import type {ModelInfo} from '../types';

export function ModelCredentials({model, onSaved}: {model: ModelInfo; onSaved: () => Promise<void>}) {
  const [key, setKey] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  if (model.provider_id === 'ollama') return null;
  return <form className="model-credentials" onSubmit={async (event) => {
    event.preventDefault(); setSaving(true); setMessage('');
    try {
      const response = await fetch(`${DEFAULT_BASE_URL}/api/providers/${encodeURIComponent(model.provider_id)}/credential`, {
        method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify({api_key: key}),
      });
      setKey('');
      if (!response.ok) throw new Error(`保存失败（${response.status}）`);
      setMessage('已保存到本机私有配置，可直接开始对话。'); await onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setSaving(false); }
  }}>
    <p>{model.status === 'missing_key' ? '尚未填写 API Key' : 'API Key 已配置'}</p>
    <label>模型 API Key<input aria-label="模型 API Key" type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)} placeholder={model.status === 'missing_key' ? '在此输入密钥' : '输入新密钥以替换已保存的密钥'} /></label>
    <button className="primary-button" type="submit" disabled={!key.trim() || saving}>{saving ? '保存中…' : '保存密钥'}</button>
    <p>仅存本机；不回显，不写入聊天或 Git。</p>{message ? <p role="status">{message}</p> : null}
  </form>;
}
