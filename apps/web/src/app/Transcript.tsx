import {formatTime} from '../lib/format';
import type {ReadingReport} from '../lib/report';
import type {TranscriptViewItem} from '../lib/transcript';
import {statusLabel} from '../statusLabels';
import type {TranscriptBlock} from '../types';
import {Icon} from '../ui/Icon';
import {AssistantTurn} from './AssistantTurn';
import {statusTone} from './ToolTrace';

export type TranscriptProps = {
  runStatus?: string;
  items: TranscriptViewItem[];
  busy: boolean;
  loading: boolean;
  conversationLoading: boolean;
  error: string | null;
  backendOnline: boolean;
  onDismissError: () => void;
  onReconnect: () => void;
  onExample: (text: string) => void;
  onToggleBlock: (blockId: string) => void;
  onCopy: (text: string) => void;
  onEdit: (messageId: string, text: string) => void;
  onDelete: (messageId: string) => void;
  onRegenerate: (userMessageId?: string) => void;
  onSwitchVersion: (messageId: string, direction: -1 | 1) => void;
  onReadReport: (report: ReadingReport) => void;
  conversationId?: string;
  editingMessageId: string | null;
  onCancelEdit: () => void;
  editText: string;
  onEditTextChange: (text: string) => void;
  onSaveEdit: () => void;
  onOpenConversation?: (conversationId: string) => void | Promise<void>;
};

const EXAMPLES: Array<{icon: string; text: string}> = [
  {icon: 'i-file', text: '读取工作区里的 CSV 并总结'},
  {icon: 'i-chart', text: '获取当前市场简报'},
  {icon: 'i-search', text: '帮我查一个历史会话'},
];

function LoadingLine({label}: {label: string}): JSX.Element {
  return (
    <p className="loading">
      <span className="dots" aria-hidden="true"><i /> <i /> <i /></span>{label}
    </p>
  );
}

function EmptyThread({onExample}: {onExample: (text: string) => void}): JSX.Element {
  return (
    <section className="empty">
      <span className="empty-mark" aria-hidden="true"><Icon name="i-spark" className="ico-lg" /></span>
      <h2>欢迎，可以直接提问</h2>
      <p>也可以从下面的示例开始。</p>
      <div className="empty-list">
        {EXAMPLES.map((example) => (
          <button key={example.text} type="button" className="empty-item" onClick={() => onExample(example.text)}>
            <Icon name={example.icon} />
            <span>{example.text}</span>
            <Icon name="i-chev-right" className="ico-sm" />
          </button>
        ))}
      </div>
      <p className="empty-foot">右上角可以打开工作台：上传文件、管理技能；生成的报告会在右侧预览。</p>
    </section>
  );
}

function TranscriptItem({
  block,
  busy,
  onToggleBlock,
  onCopy,
  onEdit,
  onDelete,
  editingMessageId,
  onCancelEdit,
  editText,
  onEditTextChange,
  onSaveEdit,
}: {
  block: TranscriptBlock;
  busy: boolean;
  onToggleBlock: (blockId: string) => void;
  onCopy: (text: string) => void;
  onEdit: (messageId: string, text: string) => void;
  onDelete: (messageId: string) => void;
  editingMessageId: string | null;
  onCancelEdit: () => void;
  editText: string;
  onEditTextChange: (text: string) => void;
  onSaveEdit: () => void;
}): JSX.Element {
  const messageId = block.messageId ?? null;
  if (block.kind === 'user' && messageId && messageId === editingMessageId) {
    return (
      <article className="msg msg-user" data-turn={block.id}>
        <div className="u-wrap u-edit">
          <textarea
            className="textarea"
            rows={3}
            aria-label="编辑这条用户消息"
            value={editText}
            autoFocus
            onChange={(event) => onEditTextChange(event.target.value)}
          />
          <div className="u-edit-acts">
            <button type="button" className="btn btn-primary btn-sm" onClick={onSaveEdit}>保存并重新生成</button>
            <button type="button" className="btn btn-quiet btn-sm" onClick={onCancelEdit}>取消</button>
          </div>
        </div>
      </article>
    );
  }
  const collapsed = Boolean(block.collapsible && block.collapsed);
  const head = (
    <header className="msg-head">
      <span className="msg-who">{block.label}</span>
      {block.status ? <span className="msg-status" data-tone={statusTone(block.status)}>{statusLabel(block.status)}</span> : null}
      {block.createdAt ? <span className="meta">· {formatTime(block.createdAt)}</span> : null}
      {block.collapsible ? (
        <button type="button" className="msg-act" aria-expanded={!collapsed} onClick={() => onToggleBlock(block.id)}>
          {collapsed ? '展开' : '折叠'}
        </button>
      ) : null}
    </header>
  );
  if (block.kind === 'error') {
    return (
      <article className="msg msg-error" data-turn={block.id}>
        {head}
        <div className="turn-error"><Icon name="i-alert" /><span>{block.text}</span></div>
      </article>
    );
  }
  return (
    <article className={`msg msg-${block.kind}`} data-turn={block.id}>
      {block.kind === 'meta' ? head : null}
      {collapsed ? null : (
        <div className="u-wrap">
          <div className="u-body">{block.text}</div>
          {block.kind === 'user' && messageId ? (
            <div className="msg-acts">
              <button
                type="button"
                className="msg-act"
                disabled={!block.text}
                onClick={() => onCopy(block.text)}
                title="复制内容"
              ><Icon name="i-copy" className="ico-sm" />复制</button>
              <button
                type="button"
                className="msg-act"
                disabled={busy}
                onClick={() => onEdit(messageId, block.text)}
                title="编辑并重新发送"
              ><Icon name="i-pencil" className="ico-sm" />编辑</button>
              <button
                type="button"
                className="msg-act"
                disabled={busy}
                onClick={() => onDelete(messageId)}
                title="删除此消息"
              ><Icon name="i-trash" className="ico-sm" />删除</button>
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}

export function Transcript({
  runStatus,
  items,
  busy,
  loading,
  conversationLoading,
  error,
  backendOnline,
  onDismissError,
  onReconnect,
  onExample,
  onToggleBlock,
  onCopy,
  onEdit,
  onDelete,
  onRegenerate,
  onSwitchVersion,
  onReadReport,
  conversationId,
  editingMessageId,
  onCancelEdit,
  editText,
  onEditTextChange,
  onSaveEdit,
  onOpenConversation,
}: TranscriptProps): JSX.Element {
  return (
    <>
      <main className="transcript" id="transcript" aria-busy={conversationLoading}>
        <div className="thread" id="thread">
          {loading ? <LoadingLine label="正在加载…" /> : null}
          {!loading && conversationLoading ? <LoadingLine label="正在载入会话…" /> : null}
          {!loading && items.length === 0 ? <EmptyThread onExample={onExample} /> : null}
          {items.map((item) => (
            item.kind === 'assistant-group'
              ? (
                <AssistantTurn
                  key={item.id}
                  item={item}
                  runStatus={items[items.length-1]?.id===item.id?runStatus:undefined}
                  busy={busy}
                  onToggleBlock={onToggleBlock}
                  onCopy={onCopy}
                  onDelete={onDelete}
                  onRegenerate={onRegenerate}
                  onSwitchVersion={onSwitchVersion}
                  onReadReport={onReadReport}
                  onOpenConversation={onOpenConversation}
                  conversationId={conversationId}
                />
              )
              : (
                <TranscriptItem
                  key={item.id}
                  block={item}
                  busy={busy}
                  onToggleBlock={onToggleBlock}
                  onCopy={onCopy}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  editingMessageId={editingMessageId}
                  onCancelEdit={onCancelEdit}
                  editText={editText}
                  onEditTextChange={onEditTextChange}
                  onSaveEdit={onSaveEdit}
                />
              )
          ))}
        </div>
      </main>
      {error ? (
        <div className="notice-bar notice-err" role="alert">
          <Icon name="i-alert" />
          <span className="grow">{error}</span>
          {!backendOnline ? (
            <button type="button" className="btn btn-secondary btn-sm" disabled={loading} onClick={onReconnect}>重新连接</button>
          ) : null}
          <button type="button" className="btn btn-quiet btn-icon btn-sm" aria-label="关闭错误提示" onClick={onDismissError}>
            <Icon name="i-x" className="ico-sm" />
          </button>
        </div>
      ) : null}
    </>
  );
}
