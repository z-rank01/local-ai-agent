import {useEffect, useRef} from 'react';
import type {ReactNode} from 'react';
import type {KeyboardEvent as ReactKeyboardEvent} from 'react';
import {Icon} from '../ui/Icon';

export type ComposerProps = {
  runStatus?: ReactNode;
  contextLoading?: boolean;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  versionBusy: boolean;
  editing: boolean;
  onCancelEdit: () => void;
  attachments: string[];
  onDetach: (path: string) => void;
  onUpload: (files: FileList) => void;
  statusText: string;
  placeholder: string;
};

const MAX_INPUT_HEIGHT = 200;

const baseName = (path: string): string => path.split('/').filter(Boolean).pop() ?? path;

/* 底部输入胶囊：附件 chip 行 + 文本域 + 工具条。
   Enter 发送（Shift+Enter 换行），中文输入法组字中的 Enter 不发送；
   生成中只把发送切成停止，文本域照常可继续起草。 */
export function Composer({
  runStatus,
  contextLoading = false,
  value,
  onChange,
  onSend,
  onStop,
  busy,
  versionBusy,
  editing,
  onCancelEdit,
  attachments,
  onDetach,
  onUpload,
  statusText,
  placeholder,
}: ComposerProps): JSX.Element {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  /* 文本域随内容长高（上限 200px，超过由 .composer-input 的 max-height 接管滚动） */
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(MAX_INPUT_HEIGHT, node.scrollHeight)}px`;
  }, [value]);

  /* 进入编辑态就把焦点交给这条消息的文本，省一次点击 */
  useEffect(() => {
    if (editing) {
      textareaRef.current?.focus();
    }
  }, [editing]);

  const canSend = value.trim().length > 0 || attachments.length > 0;
  const sendDisabled = !canSend || versionBusy;

  const submit = () => {
    if (busy || sendDisabled) return;
    onSend();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    /* 生成中同样吃掉回车：不发也不换行，避免把草稿顶断 */
    event.preventDefault();
    submit();
  };

  return (
    <footer className="composer-wrap">
      {runStatus}
      <div className="composer">
        {attachments.length > 0 ? (
          <div className="attach-row">
            {attachments.map((path) => (
              <span className="attach" key={path} title={path}>
                {baseName(path)}
                <button
                  type="button"
                  title="从本次对话移除"
                  aria-label={`从本次对话移除：${path}`}
                  onClick={() => onDetach(path)}
                >
                  <Icon name="i-x" className="ico-sm" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          className="composer-input"
          rows={1}
          aria-label="消息输入框"
          disabled={contextLoading}
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-bar">
          <span className="composer-hint">
            {busy ? (
              <>
                <span className="dots" aria-hidden="true"><i /><i /><i /></span>{' '}
              </>
            ) : null}
            {statusText}
          </span>
          <button
            type="button"
            className="composer-tool"
            title="上传文件到工作区"
            aria-label="上传文件到工作区"
            disabled={busy || versionBusy}
            onClick={() => fileRef.current?.click()}
          >
            <Icon name="i-upload" />
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            tabIndex={-1}
            onChange={(event) => {
              const files = event.currentTarget.files;
              if (files && files.length > 0) {
                onUpload(files);
              }
              event.currentTarget.value = '';
            }}
          />
          {editing ? (
            <button type="button" className="btn btn-quiet btn-sm" onClick={onCancelEdit}>
              取消编辑
            </button>
          ) : null}
          <button
            type="button"
            className={busy ? 'send-btn stop' : 'send-btn'}
            title={busy ? '停止生成' : editing ? '保存并重试' : '发送'}
            aria-label={busy ? '停止生成' : editing ? '保存并重试' : '发送'}
            disabled={!busy && sendDisabled}
            onClick={busy ? onStop : submit}
          >
            <Icon name={busy ? 'i-stop' : 'i-send'} />
          </button>
        </div>
      </div>
    </footer>
  );
}
