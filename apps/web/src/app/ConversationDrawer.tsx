import {useLayerFocus} from '../ui/useLayerFocus';
import {Dropdown} from '../ui/Dropdown';
import {Icon} from '../ui/Icon';
import {formatTime} from '../lib/format';
import {PackageTasks} from '../panels/PackageTasks';
import type {ConversationSummary, Workspace} from '../types';

export type ConversationDrawerProps = {
  open: boolean;
  onClose: () => void;
  conversations: ConversationSummary[];
  workspaces: Workspace[]; workspaceId: string; onSelectWorkspace: (id: string) => void; busy: boolean;
  activeId: string | null;
  filter: string;
  onFilterChange: (value: string) => void;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRename: (conversation: ConversationSummary) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onExport: (id: string) => void;
  backendOnline: boolean;
  onShutdown: () => void;
  shutdownPending: boolean;
};

const conversationTitle = (conversation: ConversationSummary): string =>
  conversation.title.trim() || '未命名会话';

/* 覆盖式会话抽屉：新建 / 搜索 / 列表 / 底部服务状态与唯一退出入口。
   行主体是真按钮，导出·重命名·删除是三个并列真按钮（不嵌 role=button）。 */
export function ConversationDrawer({
  open,
  onClose,
  conversations,
  workspaces, workspaceId, onSelectWorkspace, busy,
  activeId,
  filter,
  onFilterChange,
  onOpen,
  onNew,
  onRename,
  onDelete,
  onExport,
  backendOnline,
  onShutdown,
  shutdownPending,
}: ConversationDrawerProps): JSX.Element {
  const query = filter.trim().toLowerCase();

  const visible = conversations;
  const layerRef = useLayerFocus(open, true);

  return (
    <>
      {open ? <div className="scrim on" aria-hidden="true" onClick={onClose} /> : null}
      <aside
        ref={layerRef}
        className={open ? 'drawer open' : 'drawer'}
        role="dialog"
        aria-modal="true"
        aria-label="会话列表"
      >
        <div className="drawer-head">
          <span className="drawer-brand">
            <span className="drawer-logo" aria-hidden="true">LA</span>
            <span className="drawer-id">
              <span className="drawer-name">Local AI Agent</span>
              <span className="drawer-sub">Web Chat · 本机服务</span>
            </span>
          </span>
          <button
            type="button"
            className="tb-btn"
            title="关闭会话列表"
            aria-label="关闭会话列表"
            onClick={onClose}
          >
            <Icon name="i-x" />
          </button>
        </div>
        <button type="button" className="btn btn-dark btn-block drawer-new" disabled={busy} onClick={onNew}>
          <Icon name="i-plus" className="ico-sm" />新建会话
        </button>
        <div className="drawer-workspace"><Dropdown value={workspaceId} options={workspaces.map(w => ({value: w.id, label: w.name}))} onChange={onSelectWorkspace} disabled={busy} ariaLabel="会话所属工作区" /></div>
        <div className="search-box">
          <Icon name="i-search" />
          <input
            className="input"
            type="search"
            autoComplete="off"
            placeholder="搜索所有工作区的会话…"
            aria-label="搜索会话"
            value={filter}
            onChange={(event) => onFilterChange(event.target.value)}
          />
        </div>
        <div className="conv-scroll">
          <div className="conv-count">{visible.length} 个会话</div>
          {visible.length > 0 ? (
            <ul>
              {visible.map((conversation) => {
                const title = conversationTitle(conversation);
                const time = formatTime(conversation.updated_at);
                return (
                  <li key={conversation.id}>
                    <div
                      className="conv"
                      aria-current={conversation.id === activeId ? 'true' : undefined}
                    >
                      <button
                        type="button"
                        className="conv-open"
                        disabled={busy}
                        onClick={() => onOpen(conversation.id)}
                      >
                        <span className="conv-title">{title}</span>
                        <span className="conv-meta">{conversation.workspace_name}</span>
                        {conversation.search_snippet && <span className="conv-meta">{conversation.search_snippet}</span>}
                        <span className="conv-meta">
                          {conversation.model ? <span>{conversation.model}</span> : null}
                          {conversation.model && time ? <span>·</span> : null}
                          {time ? <span>{time}</span> : null}
                        </span>
                      </button>
                      <span className="conv-acts">
                        <button
                          type="button"
                          title="导出 Markdown"
                          aria-label={`导出会话：${title}`}
                          onClick={() => onExport(conversation.id)}
                        >
                          <Icon name="i-download" className="ico-sm" />
                        </button>
                        <button
                          type="button"
                          title="重命名"
                          aria-label={`重命名会话：${title}`}
                          disabled={busy}
                          onClick={() => onRename(conversation)}
                        >
                          <Icon name="i-pencil" className="ico-sm" />
                        </button>
                        <button
                          type="button"
                          title="删除"
                          aria-label={`删除会话：${title}`}
                          disabled={busy}
                          onClick={() => onDelete(conversation)}
                        >
                          <Icon name="i-x" className="ico-sm" />
                        </button>
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="conv-empty">
              {query ? `没有匹配「${filter.trim()}」的会话` : '还没有会话，点上方「新建会话」开始。'}
            </p>
          )}
        </div>
        <footer className="drawer-foot">
          <PackageTasks />
          <div className="online-row">
            <span className={backendOnline ? 'dot ok' : 'dot warn'} aria-hidden="true" />
            <span>{backendOnline ? '在线 · 服务运行中' : '离线 · 后端未连接'}</span>
          </div>
          <button
            type="button"
            className="logout-btn"
            title="退出并停止全部服务"
            disabled={shutdownPending}
            onClick={onShutdown}
          >
            <Icon name="i-logout" className="ico-sm" />
            {shutdownPending ? '正在退出…' : '退出'}
          </button>
        </footer>
      </aside>
    </>
  );
}
