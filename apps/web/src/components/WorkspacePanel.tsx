import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  fetchWorkspacePreview,
  fetchWorkspaceTree,
  uploadWorkspaceFile,
  workspaceRawUrl,
} from '../api';
import type {WorkspaceEntry, WorkspaceFilePreview} from '../types';

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

type WorkspacePanelProps = {
  attachedPaths: string[];
  onAttach: (path: string) => void;
  onDetach: (path: string) => void;
  onError: (message: string) => void;
};

export function WorkspacePanel({attachedPaths, onAttach, onDetach, onError}: WorkspacePanelProps) {
  const [currentPath, setCurrentPath] = useState('/workspace');
  const [root, setRoot] = useState('/workspace');
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [preview, setPreview] = useState<WorkspaceFilePreview | null>(null);
  const [selectedPath, setSelectedPath] = useState<string>('');
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [navigationMotion, setNavigationMotion] = useState<'forward' | 'back' | 'refresh'>('refresh');

  const selectedAttached = useMemo(
    () => (selectedPath ? attachedPaths.includes(selectedPath) : false),
    [attachedPaths, selectedPath],
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

  const previewFile = useCallback(async (path: string) => {
    setSelectedPath(path);
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
    void loadTree(currentPath);
  }, [currentPath, loadTree]);

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

  const rawUrl = preview ? workspaceRawUrl(preview.path) : '';
  const canShowImage = Boolean(preview?.is_binary && preview.mime_type?.startsWith('image/'));

  return (
    <section className="workspace-panel">
      <div className="workspace-header">
        <div>
          <h2>工作区</h2>
          <span title={root}>{pathLabel(root)}</span>
        </div>
        <button type="button" className="ghost-button tiny" onClick={() => { setNavigationMotion('refresh'); void loadTree(root); }} disabled={loadingTree}>
          刷新
        </button>
      </div>

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

      <div key={`${root}:${navigationMotion}`} className={`workspace-list workspace-list-${navigationMotion}`} aria-busy={loadingTree}>
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
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="workspace-preview">
        <div className="workspace-preview-header">
          <h3>预览</h3>
          {preview ? <span>{formatBytes(preview.size)} · {formatShortTime(preview.modified_at)}</span> : null}
        </div>
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
      </div>
    </section>
  );
}
