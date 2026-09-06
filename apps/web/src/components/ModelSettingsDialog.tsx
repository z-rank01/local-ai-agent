import {useEffect, useRef} from 'react';
import {createPortal} from 'react-dom';
import type {AppStatus, ModelInfo} from '../types';
import {ModelCredentials} from './ModelCredentials';

export function ModelSettingsDialog({model, status, onClose, onSaved}: {
  model?: ModelInfo; status: AppStatus | null; onClose: () => void; onSaved: () => Promise<void>;
}) {
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
          {model.provider_id === 'ollama'
            ? <p>本地 Ollama 模型无需 API Key。请确保 Ollama 服务已启动并已下载所选模型。</p>
            : <ModelCredentials key={model.provider_id} model={model} onSaved={onSaved} />}
        </> : <p>模型列表尚未加载，请关闭窗口后重试。</p>}
        <div className="model-settings-notes">
          <p>{status?.workspace_cloud_allowed ? '云端会接收聊天与本测试工作区的工具结果，请只使用允许发送的资料。' : '云端当前仅可聊天，本地文件与工具结果尚未授权发送。'}</p>
          <p>本批云端请求：{status?.model_calls_used ?? 0} / {status?.model_call_limit || '不限'}（包含失败尝试）</p>
        </div>
      </div>
      <footer className="model-settings-footer"><button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
    </dialog>, document.body,
  );
}
