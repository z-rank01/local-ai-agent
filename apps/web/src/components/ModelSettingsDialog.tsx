import {useEffect, useRef, useState} from 'react';
import {DEFAULT_BASE_URL} from '../api';
import {createPortal} from 'react-dom';
import type {AppStatus, ModelInfo} from '../types';
import {ModelCredentials} from './ModelCredentials';

export function ModelSettingsDialog({model, status, busy = false, onClose, onSaved}: {
  model?: ModelInfo; status: AppStatus | null; busy?: boolean; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const changeSetting = async (change: {thinking_enabled?: boolean; workspace_cloud_allowed?: boolean}) => {
    if (!model || !status) return;
    setSaving(true); setNotice('');
    try {
      const response = await fetch(`${DEFAULT_BASE_URL}/api/model-settings`, {
        method: 'PATCH', headers: {'content-type': 'application/json'},
        body: JSON.stringify({model_id: model.id, workspace_path: status.workspace_path, ...change}),
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(typeof body.detail === 'string' ? body.detail : '设置保存失败');
      }
      await onSaved();
      setNotice('已保存，从下一条消息生效。请回到聊天继续原任务。');
    } catch (error) {setNotice(error instanceof Error ? error.message : '设置保存失败');}
    finally {setSaving(false);}
  };
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current!;
    const previousFocus = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);

  return createPortal(
    <dialog ref={dialogRef} className="model-settings-dialog" aria-labelledby="model-settings-title"
      onCancel={(event) => {event.preventDefault(); onClose();}}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
      }}>
      <header className="model-settings-header">
        <h2 id="model-settings-title">模型设置</h2>
        <button type="button" className="ghost-button model-settings-close" aria-label="关闭模型设置" autoFocus onClick={onClose}>×</button>
      </header>
      <div className="model-settings-body">
        {model ? <>
          <div className="model-settings-current"><span>当前模型</span><strong>{model.name}</strong><span>{model.provider_name}</span></div>
          <section className="model-capability-settings" aria-label="模型能力">
            <label className="capability-toggle">
              <span>思考输出</span>
              <input type="checkbox" role="switch" aria-label="思考输出" checked={model.thinking_enabled}
                disabled={saving || busy || !status || !model.thinking_supported}
                onChange={event => void changeSetting({thinking_enabled: event.target.checked})} />
            </label>
            <p>{model.thinking_supported ? '开启后请求模型返回思考内容，并在对话中折叠展示；可能增加响应时间和费用。按模型保存，独立于工具开关。' : '当前模型未提供思考开关；若模型返回思考内容，仍会照常展示。'}</p>
            {model.provider_id !== 'ollama' ? <>
              <label className="capability-toggle">
                <span>允许云端使用当前工作区工具</span>
                <input type="checkbox" role="switch" aria-label="允许云端使用当前工作区工具" checked={status?.workspace_cloud_allowed ?? false}
                  disabled={saving || busy || !status}
                  onChange={event => void changeSetting({workspace_cloud_allowed: event.target.checked})} />
              </label>
              <p>开启即允许云端模型调用工作区工具；读取的文件内容与工具结果可能发送给所选云端服务，工具可执行代码及修改文件。授权适用于此工作区的所有云端模型及会话，重启后保留。</p>
              <p className="capability-workspace">工作区：{status?.workspace_path ?? '正在连接…'}</p>
              <p>关闭后停止后续工具调用；此前已发送的内容不会撤回。若任务要求只读，仍应遵守只读要求。</p>
            </> : <p>本地模型可使用工作区工具，无需云端授权。</p>}
            {busy ? <p>正在生成回答，开关暂不可修改；停止或完成后再更改。</p> : null}
            {notice ? <p role="status">{notice}</p> : null}
          </section>
          {model.provider_id === 'ollama'
            ? <p>本地 Ollama 模型无需 API Key。请确保 Ollama 服务已启动并已下载所选模型。</p>
            : <ModelCredentials key={model.provider_id} model={model} onSaved={onSaved} />}
        </> : <p>模型列表尚未加载，请关闭窗口后重试。</p>}
        <div className="model-settings-notes">
          <p>{status?.workspace_cloud_allowed ? '云端会接收聊天与当前工作区的工具结果，请只使用允许发送的资料。' : '云端当前仅可聊天，本地文件与工具结果尚未授权发送。'}</p>
          <p>本批云端请求：{status?.model_calls_used ?? 0} / {status?.model_call_limit || '不限'}（包含失败尝试）</p>
        </div>
      </div>
      <footer className="model-settings-footer"><button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
    </dialog>, document.body,
  );
}
