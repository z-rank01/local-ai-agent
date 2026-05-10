import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  deleteWorkspaceFile,
  fetchWorkspacePreview,
  fetchWorkspaceTrash,
  fetchWorkspaceTree,
  restoreWorkspaceTrashItem,
  uploadWorkspaceFile,
  workspaceRawUrl,
} from '../api';
import type {WorkspaceEntry, WorkspaceFilePreview, WorkspaceTrashEntry} from '../types';

function formatBytes(value?: number | null): string {
  if (value == null) {
    return '—';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[index]}`;
}

function formatShortTime(value?: string | null): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

function parentPath(path: string): string | null {
  if (!path || path === '/workspace') {
    return null;
  }
  const trimmed = path.replace(/\/$/, '');
  const index = trimmed.lastIndexOf('/');
  if (index <= '/workspace'.length) {
    return '/workspace';
  }
  return trimmed.slice(0, index);
}

function pathLabel(path: string): string {
  return path.replace('/workspace', 'workspace') || 'workspace';
}

function entryIcon(entry: WorkspaceEntry): string {
  if (entry.kind === 'directory') {
    return '📁';
  }
  if (entry.mime_type?.startsWith('image/')) {
    return '🖼️';
  }
  if (entry.mime_type === 'application/pdf') {
    return '📕';
  }
  return '📄';
}

function trashEntryIcon(entry: WorkspaceTrashEntry): string {
  if (entry.item_type === 'directory') {
    return '📁';
  }
  return '🗑️';
}

type WorkspacePanelProps = {
  attachedPaths: string[];
  onAttach: (path: string) => void;
  onDetach: (path: string) => void;
  onError: (message: string) => void;
};

export function WorkspacePanel({attachedPaths, onAttach, onDetach, onError}: WorkspacePanelProps) {
  const [viewMode, setViewMode] = useState<'workspace' | 'trash'>('workspace');
  const [currentPath, setCurrentPath] = useState('/workspace');
  const [root, setRoot] = useState('/workspace');
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [preview, setPreview] = useState<WorkspaceFilePreview | null>(null);
  const [trashItems, setTrashItems] = useState<WorkspaceTrashEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string>('');
  const [selectedTrashOperationId, setSelectedTrashOperationId] = useState('');
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingTrash, setLoadingTrash] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingPath, setDeletingPath] = useState('');
  const [restoringOperationId, setRestoringOperationId] = useState('');
  const [notice, setNotice] = useState('');
  const [navigationMotion, setNavigationMotion] = useState<'forward' | 'back' | 'refresh'>('refresh');

  const selectedAttached = useMemo(
    () => (selectedPath ? attachedPaths.includes(selectedPath) : false),
    [attachedPaths, selectedPath],
  );
  const selectedTrashEntry = useMemo(
    () => trashItems.find((item) => item.operation_id === selectedTrashOperationId) ?? null,
    [selectedTrashOperationId, trashItems],
  );

  const loadTree = useCallback(async (nextPath: string) => {
    setLoadingTree(true);
    try {
      const tree = await fetchWorkspaceTree(nextPath);
      setRoot(tree.root);
      setEntries(tree.entries);
      setCurrentPath(tree.root);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingTree(false);
    }
  }, [onError]);

  const loadTrash = useCallback(async () => {
    setLoadingTrash(true);
    try {
      const response = await fetchWorkspaceTrash();
      setTrashItems(response.items);
      setSelectedTrashOperationId((current) =>
        response.items.some((item) => item.operation_id === current)
          ? current
          : response.items[0]?.operation_id ?? '',
      );
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingTrash(false);
    }
  }, [onError]);

  const previewFile = useCallback(async (path: string) => {
    setSelectedPath(path);
    setNotice('');
    setLoadingPreview(true);
    try {
      const next = await fetchWorkspacePreview(path);
      setPreview(next);
    } catch (err) {
      setPreview(null);
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingPreview(false);
    }
  }, [onError]);

  useEffect(() => {
    if (viewMode === 'workspace') {
      void loadTree(currentPath);
    }
  }, [currentPath, loadTree, viewMode]);

  useEffect(() => {
    if (viewMode === 'trash') {
      void loadTrash();
    }
  }, [loadTrash, viewMode]);

  const openEntry = (entry: WorkspaceEntry) => {
    if (entry.kind === 'directory') {
      setNavigationMotion('forward');
      setPreview(null);
      setSelectedPath('');
      setCurrentPath(entry.path);
      return;
    }
    void previewFile(entry.path);
  };

  const uploadFiles = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? []);
    if (!files.length) {
      return;
    }

    setUploading(true);
    setNotice('');
    let lastUploadedPath = '';
    try {
      for (const file of files) {
        const result = await uploadWorkspaceFile(file, root);
        lastUploadedPath = result.attachment.workspace_path;
        onAttach(result.attachment.workspace_path);
      }
      setNavigationMotion('refresh');
      await loadTree(root);
      if (lastUploadedPath) {
        await previewFile(lastUploadedPath);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const goParent = () => {
    const next = parentPath(root);
    if (next) {
      setNavigationMotion('back');
      setPreview(null);
      setSelectedPath('');
      setCurrentPath(next);
    }
  };

  const switchToWorkspace = () => {
    setViewMode('workspace');
    setNotice('');
  };

  const switchToTrash = () => {
    setViewMode('trash');
    setSelectedPath('');
    setPreview(null);
    setNotice('');
  };

  const attachSelected = () => {
    if (selectedPath) {
      onAttach(selectedPath);
    }
  };

  const detachSelected = () => {
    if (selectedPath) {
      onDetach(selectedPath);
    }
  };

  const deleteFile = async (path: string, name: string) => {
    if (!window.confirm(`把「${name}」移入回收站？`)) {
      return;
    }

    setDeletingPath(path);
    setNotice('');
    try {
      const result = await deleteWorkspaceFile(path);
      if (attachedPaths.includes(path)) {
        onDetach(path);
      }
      if (selectedPath === path) {
        setSelectedPath('');
        setPreview(null);
      }
      setNavigationMotion('refresh');
      await loadTree(root);
      setNotice(`已移入回收站 · ${result.operation_id ?? name}`);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingPath('');
    }
  };

  const restoreTrashItem = async (item: WorkspaceTrashEntry) => {
    if (!window.confirm(`恢复「${item.name}」到原始位置？`)) {
      return;
    }

    setRestoringOperationId(item.operation_id);
    setNotice('');
    try {
      const result = await restoreWorkspaceTrashItem(item.operation_id);
      await loadTrash();
      setNotice(`已恢复 · ${result.workspace_path || item.workspace_path}`);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setRestoringOperationId('');
    }
  };

  const rawUrl = preview ? workspaceRawUrl(preview.path) : '';
  const canShowImage = Boolean(preview?.is_binary && preview.mime_type?.startsWith('image/'));
  const listBusy = viewMode === 'workspace' ? loadingTree : loadingTrash;
  const listKey = viewMode === 'workspace' ? `${root}:${navigationMotion}` : `trash:${trashItems.length}`;
  const listClassName = `workspace-list workspace-list-${viewMode === 'workspace' ? navigationMotion : 'refresh'}`;

  return (
    <section className="workspace-panel">
      <div className="workspace-header">
        <div>
          <h2>{viewMode === 'workspace' ? '工作区' : '回收站'}</h2>
          <span title={viewMode === 'workspace' ? root : '已删除文件'}>
            {viewMode === 'workspace' ? pathLabel(root) : `${trashItems.length} 项`}
          </span>
        </div>
        <div className="workspace-mode-switch">
          <button
            type="button"
            className={`ghost-button tiny workspace-mode-button${viewMode === 'workspace' ? ' active' : ''}`}
            onClick={switchToWorkspace}
          >
            文件
          </button>
          <button
            type="button"
            className={`ghost-button tiny workspace-mode-button${viewMode === 'trash' ? ' active' : ''}`}
            onClick={switchToTrash}
          >
            回收站
          </button>
          <button
            type="button"
            className="ghost-button tiny"
            onClick={() => {
              setNotice('');
              if (viewMode === 'workspace') {
                setNavigationMotion('refresh');
                void loadTree(root);
              } else {
                void loadTrash();
              }
            }}
            disabled={listBusy}
          >
            刷新
          </button>
        </div>
      </div>

      {viewMode === 'workspace' ? (
        <div className="workspace-actions">
          <button type="button" className="ghost-button tiny" onClick={goParent} disabled={!parentPath(root)}>
            上级
          </button>
          <label className={`upload-button${uploading ? ' disabled' : ''}`}>
            {uploading ? '上传中...' : '上传文件'}
            <input
              type="file"
              multiple
              disabled={uploading}
              onChange={(event) => {
                void uploadFiles(event.currentTarget.files);
                event.currentTarget.value = '';
              }}
            />
          </label>
        </div>
      ) : (
        <div className="workspace-actions">
          <p className="workspace-actions-hint">已删除文件会先进入回收站，可在这里恢复到原始位置。</p>
        </div>
      )}

      {notice ? <div className="workspace-note">{notice}</div> : null}

      <div key={listKey} className={listClassName} aria-busy={listBusy}>
        {viewMode === 'workspace' ? (
          <>
            {loadingTree ? <div className="workspace-empty">正在读取目录...</div> : null}
            {!loadingTree && entries.length === 0 ? <div className="workspace-empty">目录为空</div> : null}
            {entries.map((entry) => {
              const attached = attachedPaths.includes(entry.path);
              return (
                <button
                  type="button"
                  key={entry.path}
                  className={`workspace-entry${entry.path === selectedPath ? ' selected' : ''}`}
                  onClick={() => openEntry(entry)}
                  title={entry.path}
                >
                  <span className="workspace-entry-icon">{entryIcon(entry)}</span>
                  <span className="workspace-entry-main">
                    <strong>{entry.name}</strong>
                    <em>{entry.kind === 'directory' ? '目录' : `${formatBytes(entry.size)} · ${entry.mime_type ?? '文件'}`}</em>
                  </span>
                  {entry.kind === 'file' ? (
                    <span className="workspace-entry-actions">
                      <span
                        className={`workspace-attach-pill${attached ? ' active' : ''}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (attached) {
                            onDetach(entry.path);
                          } else {
                            onAttach(entry.path);
                          }
                        }}
                      >
                        {attached ? '已附加' : '附加'}
                      </span>
                      <span
                        className={`workspace-delete-pill${deletingPath ? ' disabled' : ''}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (!deletingPath) {
                            void deleteFile(entry.path, entry.name);
                          }
                        }}
                      >
                        {deletingPath === entry.path ? '删除中...' : '删除'}
                      </span>
                    </span>
                  ) : null}
                </button>
              );
            })}
          </>
        ) : (
          <>
            {loadingTrash ? <div className="workspace-empty">正在读取回收站...</div> : null}
            {!loadingTrash && trashItems.length === 0 ? <div className="workspace-empty">回收站为空</div> : null}
            {trashItems.map((item) => (
              <button
                type="button"
                key={item.operation_id}
                className={`workspace-entry${item.operation_id === selectedTrashOperationId ? ' selected' : ''}`}
                onClick={() => {
                  setSelectedTrashOperationId(item.operation_id);
                  setNotice('');
                }}
                title={item.workspace_path || item.original_path}
              >
                <span className="workspace-entry-icon">{trashEntryIcon(item)}</span>
                <span className="workspace-entry-main">
                  <strong>{item.name}</strong>
                  <em>{`${formatShortTime(item.deleted_at) || '刚刚'} · ${item.workspace_path || item.relative_path}`}</em>
                </span>
                <span className="workspace-entry-actions">
                  <span
                    className={`workspace-restore-pill${restoringOperationId ? ' disabled' : ''}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (!restoringOperationId && item.exists_in_trash) {
                        void restoreTrashItem(item);
                      }
                    }}
                  >
                    {restoringOperationId === item.operation_id ? '恢复中...' : '恢复'}
                  </span>
                </span>
              </button>
            ))}
          </>
        )}
      </div>

      <div className="workspace-preview">
        <div className="workspace-preview-header">
          <h3>{viewMode === 'workspace' ? '预览' : '回收站详情'}</h3>
          {viewMode === 'workspace'
            ? preview ? <span>{formatBytes(preview.size)} · {formatShortTime(preview.modified_at)}</span> : null
            : selectedTrashEntry ? <span>{formatShortTime(selectedTrashEntry.deleted_at)}</span> : null}
        </div>
        {viewMode === 'workspace' ? (
          <>
            {loadingPreview ? <div className="workspace-empty">正在加载预览...</div> : null}
            {!loadingPreview && !preview ? <div className="workspace-empty">选择文件查看内容</div> : null}
            {!loadingPreview && preview ? (
              <div key={preview.path} className="preview-card">
                <div className="preview-title">
                  <strong title={preview.path}>{preview.name}</strong>
                  <span>{preview.mime_type ?? 'unknown'}{preview.encoding ? ` · ${preview.encoding}` : ''}</span>
                </div>
                <div className="preview-actions">
                  {selectedAttached ? (
                    <button type="button" className="ghost-button tiny" onClick={detachSelected}>移除附件</button>
                  ) : (
                    <button type="button" className="ghost-button tiny" onClick={attachSelected}>附加到对话</button>
                  )}
                  <button
                    type="button"
                    className="ghost-button tiny danger-button"
                    onClick={() => void deleteFile(preview.path, preview.name)}
                    disabled={Boolean(deletingPath)}
                  >
                    {deletingPath === preview.path ? '删除中...' : '删除'}
                  </button>
                  <a href={rawUrl} target="_blank" rel="noreferrer">打开</a>
                </div>
                {preview.is_binary ? (
                  canShowImage ? (
                    <img className="image-preview" src={rawUrl} alt={preview.name} />
                  ) : (
                    <p className="binary-preview">二进制文件暂不直接渲染，可打开原文件或附加给 Agent 通过工具读取/转换。</p>
                  )
                ) : (
                  <pre className="text-preview">{preview.content}</pre>
                )}
                {preview.truncated ? (
                  <p className="preview-note">预览已截断，仅显示前 {formatBytes(preview.max_bytes)}。</p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : (
          <>
            {loadingTrash ? <div className="workspace-empty">正在读取回收站...</div> : null}
            {!loadingTrash && !selectedTrashEntry ? <div className="workspace-empty">选择已删除文件查看详情</div> : null}
            {!loadingTrash && selectedTrashEntry ? (
              <div key={selectedTrashEntry.operation_id} className="preview-card">
                <div className="preview-title">
                  <strong title={selectedTrashEntry.workspace_path || selectedTrashEntry.original_path}>
                    {selectedTrashEntry.name}
                  </strong>
                  <span>{selectedTrashEntry.workspace_path || selectedTrashEntry.relative_path}</span>
                </div>
                <div className="preview-actions">
                  <button
                    type="button"
                    className="ghost-button tiny"
                    onClick={() => void restoreTrashItem(selectedTrashEntry)}
                    disabled={Boolean(restoringOperationId) || !selectedTrashEntry.exists_in_trash}
                  >
                    {restoringOperationId === selectedTrashEntry.operation_id ? '恢复中...' : '恢复到原位置'}
                  </button>
                </div>
                <div className="trash-preview-meta">
                  <div>
                    <span>删除时间</span>
                    <strong>{formatShortTime(selectedTrashEntry.deleted_at) || '—'}</strong>
                  </div>
                  <div>
                    <span>原始位置</span>
                    <strong title={selectedTrashEntry.workspace_path || selectedTrashEntry.original_path}>
                      {selectedTrashEntry.workspace_path || selectedTrashEntry.original_path || '—'}
                    </strong>
                  </div>
                  <div>
                    <span>回收站路径</span>
                    <strong title={selectedTrashEntry.trash_path}>{selectedTrashEntry.trash_path || '—'}</strong>
                  </div>
                  <div>
                    <span>状态</span>
                    <strong>{selectedTrashEntry.exists_in_trash ? '可恢复' : '文件缺失'}</strong>
                  </div>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
