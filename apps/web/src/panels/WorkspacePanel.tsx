// 每个文件请求显式携带工作区身份，目录根保持稳定映射。
import {Fragment, useCallback, useEffect, useRef, useState} from 'react';
import type {DragEvent as ReactDragEvent, ReactNode} from 'react';
import {
  deleteWorkspaceFile,
  createWorkspaceDirectory,
  renameWorkspaceFile,
  fetchWorkspacePreview,
  fetchWorkspaceTrash,
  fetchWorkspaceTree,
  restoreWorkspaceTrashItem,
  uploadWorkspaceFile,
  workspaceRawUrl,
} from '../api';
import {askConfirm, askForm} from '../lib/confirm';
import {formatBytes, formatTime, parentPath, pathLabel} from '../lib/format';
import {notify} from '../lib/notify';
import type {WorkspaceEntry, WorkspaceFilePreview, WorkspaceTrashEntry} from '../types';
import {Icon} from '../ui/Icon';
import {Dropdown} from '../ui/Dropdown';
import {Collapse} from '../ui/Collapse';
import type {Workspace} from '../types';

const WORKSPACE_ROOT = '/workspace';

type Crumb = {label: string; path: string};
type DirNode = {status: 'loading' | 'ready' | 'error'; entries: WorkspaceEntry[]};

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function timeText(value?: string | null): string {
  return value ? formatTime(value) : '';
}

function crumbsFor(path: string, WORKSPACE_ROOT: string): Crumb[] {
  const crumbs: Crumb[] = [{label: WORKSPACE_ROOT, path: WORKSPACE_ROOT}];
  if (!path.startsWith(`${WORKSPACE_ROOT}/`)) {
    return crumbs;
  }
  let acc = WORKSPACE_ROOT;
  for (const segment of path.slice(WORKSPACE_ROOT.length + 1).split('/')) {
    if (!segment) {
      continue;
    }
    acc = `${acc}/${segment}`;
    crumbs.push({label: segment, path: acc});
  }
  return crumbs;
}

function dirLabelFor(path: string): string {
  if (path === WORKSPACE_ROOT) {
    return pathLabel(WORKSPACE_ROOT);
  }
  const segments = path.split('/').filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

function typeLabel(name: string, mimeType?: string | null): string {
  const parts = name.split('.');
  const extension = parts.length > 1 ? parts[parts.length - 1] : '';
  return extension ? extension.toUpperCase() : mimeType || '文件';
}

function iconName(entry: WorkspaceEntry): string {
  if (entry.kind === 'directory') {
    return 'i-folder';
  }
  const mime = entry.mime_type ?? '';
  if (mime.startsWith('image/')) {
    return 'i-image';
  }
  if (/csv|json|markdown|text|python|javascript/.test(mime)) {
    return 'i-doc';
  }
  return 'i-file';
}

export type WorkspacePanelProps = {
  workspaceId: string; roots: Workspace['roots']; primaryPath?: string; busy: boolean; onBusyChange?: (busy: boolean) => void;
  attachedPaths: string[];
  onAttach: (path: string) => void;
  onDetach: (path: string) => void;
  onError: (message: string) => void;
};

export function WorkspacePanel(props: WorkspacePanelProps): JSX.Element {
  const {attachedPaths, onAttach, onDetach, onError, workspaceId, roots, primaryPath, busy, onBusyChange} = props;
  const [viewMode, setViewMode] = useState<'workspace' | 'trash'>('workspace');
  const [currentPath, setCurrentPath] = useState(primaryPath ?? WORKSPACE_ROOT);
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [children, setChildren] = useState<Record<string, DirNode>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<WorkspaceFilePreview | null>(null);
  const [trashItems, setTrashItems] = useState<WorkspaceTrashEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [selectedTrashOperationId, setSelectedTrashOperationId] = useState('');
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingTrash, setLoadingTrash] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingPath, setDeletingPath] = useState('');
  const [restoringOperationId, setRestoringOperationId] = useState('');
  const [dropActive, setDropActive] = useState(false);
  const primaryLoaded = useRef(Boolean(primaryPath));
  useEffect(() => {
    if (!primaryLoaded.current && primaryPath) { primaryLoaded.current = true; setCurrentPath(primaryPath); }
  }, [primaryPath]);
  useEffect(() => {
    onBusyChange?.(uploading || Boolean(deletingPath) || Boolean(restoringOperationId));
    return () => onBusyChange?.(false);
  }, [uploading, deletingPath, restoringOperationId, onBusyChange]);
  const treeSequence = useRef(0);
  const previewSequence = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectedTrashEntry =
    trashItems.find((item) => item.operation_id === selectedTrashOperationId) ?? null;
  const rootPath = roots.find(r => currentPath === r.path || currentPath.startsWith(r.path + '/'))?.path ?? WORKSPACE_ROOT;
  const upPath = currentPath === rootPath ? null : parentPath(currentPath);
  const rootName = roots.find(r => r.path === rootPath)?.name ?? "托管文件";
  const crumbs = crumbsFor(currentPath, rootPath).map((crumb, index) => index === 0 ? {...crumb, label: rootName} : crumb);
  const rawUrl = preview ? workspaceRawUrl(preview.path, workspaceId) : '';
  const listBusy = viewMode === 'workspace' ? loadingTree : loadingTrash;

  const loadTree = useCallback(
    async (nextPath: string) => {
      const sequence = ++treeSequence.current;
      setLoadingTree(true);
      try {
        const tree = await fetchWorkspaceTree(nextPath, workspaceId);
        if (sequence !== treeSequence.current) return;
        setEntries(tree.entries);
        setCurrentPath(tree.root);
        setChildren({});
        setExpanded({});
      } catch (err) {
        onError(describeError(err));
      } finally {
        if (sequence === treeSequence.current) setLoadingTree(false);
      }
    },
    [onError, workspaceId],
  );

  const loadTrash = useCallback(async () => {
    setLoadingTrash(true);
    try {
      const response = await fetchWorkspaceTrash(workspaceId);
      setTrashItems(response.items);
      setSelectedTrashOperationId((current) =>
        response.items.some((item) => item.operation_id === current)
          ? current
          : response.items[0]?.operation_id ?? '',
      );
    } catch (err) {
      onError(describeError(err));
    } finally {
      setLoadingTrash(false);
    }
  }, [onError, workspaceId]);

  const previewFile = useCallback(
    async (path: string) => {
      const sequence = ++previewSequence.current;
      setSelectedPath(path);
      setLoadingPreview(true);
      try {
        const next = await fetchWorkspacePreview(path, 200_000, workspaceId);
        if (sequence === previewSequence.current) setPreview(next);
      } catch (err) {
        if (sequence === previewSequence.current) { setPreview(null); onError(describeError(err)); }
      } finally {
        if (sequence === previewSequence.current) setLoadingPreview(false);
      }
    },
    [onError, workspaceId],
  );

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

  /* 拖入文件时浏览器默认会打开该文件；这里在窗口层统一拦截，拖放只由拖放区消费 */
  useEffect(() => {
    const block = (event: DragEvent) => {
      event.preventDefault();
    };
    window.addEventListener('dragover', block);
    window.addEventListener('drop', block);
    return () => {
      window.removeEventListener('dragover', block);
      window.removeEventListener('drop', block);
    };
  }, []);

  const navigateTo = (path: string) => {
    if (!path || path === currentPath) {
      return;
    }
    ++previewSequence.current;
    setLoadingPreview(false); setEntries([]);
    setPreview(null);
    setSelectedPath('');
    setCurrentPath(path);
  };

  useEffect(() => {
    if (currentPath.startsWith('/roots/') && !roots.some(root => currentPath === root.path || currentPath.startsWith(root.path + '/'))) {
      navigateTo(primaryPath ?? WORKSPACE_ROOT);
    }
  }, [roots, currentPath, primaryPath]);

  const openEntry = (entry: WorkspaceEntry) => {
    if (entry.kind === 'directory') {
      navigateTo(entry.path);
      return;
    }
    void previewFile(entry.path);
  };

  const goParent = () => {
    if (upPath) {
      navigateTo(upPath);
    }
  };

  const switchView = (mode: 'workspace' | 'trash') => {
    if (mode === viewMode) {
      return;
    }
    setViewMode(mode);
    if (mode === 'trash') {
      setSelectedPath('');
      setPreview(null);
    }
  };

  const refresh = () => {
    if (viewMode === 'workspace') {
      void loadTree(currentPath);
    } else {
      void loadTrash();
    }
  };

  const toggleAttach = (path: string) => {
    if (attachedPaths.includes(path)) {
      onDetach(path);
      return;
    }
    onAttach(path);
  };

  const toggleDir = async (path: string) => {
    const open = !expanded[path];
    setExpanded((prev) => ({...prev, [path]: open}));
    if (!open) {
      return;
    }
    const cached = children[path];
    if (cached && cached.status === 'ready') {
      return;
    }
    setChildren((prev) => ({...prev, [path]: {status: 'loading', entries: []}}));
    try {
      const tree = await fetchWorkspaceTree(path, workspaceId);
      setChildren((prev) => ({...prev, [path]: {status: 'ready', entries: tree.entries}}));
    } catch (err) {
      setChildren((prev) => ({...prev, [path]: {status: 'error', entries: []}}));
      onError(describeError(err));
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (busy || !files.length) {
      return;
    }
    setUploading(true);
    let lastUploadedPath = '';
    try {
      for (const file of files) {
        const result = await uploadWorkspaceFile(file, currentPath, workspaceId);
        lastUploadedPath = result.attachment.workspace_path;
        onAttach(result.attachment.workspace_path);
      }
      await loadTree(currentPath);
      if (lastUploadedPath) {
        await previewFile(lastUploadedPath);
      }
      notify(
        files.length > 1
          ? `已上传 ${files.length} 个文件并附加到对话`
          : `已上传「${files[0].name}」并附加到对话`,
      );
    } catch (err) {
      onError(describeError(err));
    } finally {
      setUploading(false);
    }
  };

  const removeFile = async (path: string, name: string) => {
    const confirmed = await askConfirm({
      title: `把「${name}」移入回收站？`,
      body: '文件会先移入回收站，可随时恢复到原始位置。',
      okLabel: '移入回收站',
      danger: true,
    });
    if (!confirmed) {
      return;
    }

    setDeletingPath(path);
    try {
      await deleteWorkspaceFile(path, workspaceId);
      if (attachedPaths.includes(path)) {
        onDetach(path);
      }
      if (selectedPath === path) {
        setSelectedPath('');
        setPreview(null);
      }
      await loadTree(currentPath);
      notify(`已移入回收站 · ${name}`);
    } catch (err) {
      onError(describeError(err));
    } finally {
      setDeletingPath('');
    }
  };

  const restoreItem = async (item: WorkspaceTrashEntry) => {
    const confirmed = await askConfirm({
      title: `恢复「${item.name}」到原始位置？`,
      okLabel: '恢复',
    });
    if (!confirmed) {
      return;
    }

    setRestoringOperationId(item.operation_id);
    try {
      const result = await restoreWorkspaceTrashItem(item.operation_id, workspaceId);
      await loadTrash();
      notify(`已恢复 · ${result.workspace_path || item.workspace_path || item.name}`);
    } catch (err) {
      onError(describeError(err));
    } finally {
      setRestoringOperationId('');
    }
  };

  const onDragOverZone = (event: ReactDragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDropActive(true);
  };

  const onDragLeaveZone = (event: ReactDragEvent<HTMLButtonElement>) => {
    const next = event.relatedTarget instanceof Node ? event.relatedTarget : null;
    if (next && event.currentTarget.contains(next)) {
      return;
    }
    setDropActive(false);
  };

  const onDropZone = (event: ReactDragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDropActive(false);
    if (uploading) {
      return;
    }
    void uploadFiles(Array.from(event.dataTransfer?.files ?? []));
  };

  const renderChildren = (path: string): ReactNode => {
    const node = children[path];
    if (!node || node.status === 'loading') {
      return <p className="ws-empty">正在读取目录…</p>;
    }
    if (node.status === 'error') {
      return <p className="ws-empty">目录读取失败，收起后可重试</p>;
    }
    if (!node.entries.length) {
      return <p className="ws-empty">空目录</p>;
    }
    return node.entries.map((entry) => renderRow(entry));
  };

  const renderRow = (entry: WorkspaceEntry): ReactNode => {
    const isDir = entry.kind === 'directory';
    const isAttached = attachedPaths.includes(entry.path);
    const isOpen = Boolean(expanded[entry.path]);
    const node = children[entry.path];
    return (
      <div className="ws-node" key={entry.path}>
        <div className="ws-row" aria-selected={entry.path === selectedPath}>
          {isDir ? (
            <button
              type="button"
              className="ws-disc"
              aria-expanded={isOpen}
              title={isOpen ? '收起' : '展开'}
              aria-label={`${entry.name}：${isOpen ? '收起' : '展开'}`}
              onClick={() => void toggleDir(entry.path)}
            >
              <Icon name="i-chev-right" className="ico-sm" />
            </button>
          ) : (
            <button
              type="button"
              className="ws-check"
              role="checkbox"
              aria-checked={isAttached}
              title={isAttached ? '取消附加' : '附加到对话'}
              aria-label={`${entry.name}：${isAttached ? '取消附加' : '附加到对话'}`}
              onClick={() => toggleAttach(entry.path)}
            >
              <Icon name="i-check" className="ico-sm" />
            </button>
          )}
          <span className="ws-ico" data-kind={entry.kind}>
            <Icon name={iconName(entry)} />
          </span>
          <button type="button" className="ws-open" title={entry.path} onClick={() => openEntry(entry)}>
            <span className="ws-name">{entry.name}</span>
            <span className="ws-meta">
              {isDir
                ? `${node?.status === 'ready' ? `${node.entries.length} 项` : '目录'} · ${
                    timeText(entry.modified_at) || '—'
                  }`
                : `${formatBytes(entry.size)} · ${typeLabel(entry.name, entry.mime_type)}`}
            </span>
          </button>
          {!isDir && <button type="button" className="btn btn-quiet btn-sm" disabled={busy} aria-label={`重命名 ${entry.name}`} onClick={async () => {
            const values = await askForm({title: '重命名文件', fields: [{key: 'name', label: '文件名', value: entry.name, required: true}]});
            if (!values) return;
            if (/[\\/]/.test(values.name) || values.name === '.' || values.name === '..') { onError('请输入文件名，不包含目录'); return; }
            try { const next = await renameWorkspaceFile(entry.path, (parentPath(entry.path) ?? currentPath) + '/' + values.name, workspaceId);
              if (attachedPaths.includes(entry.path)) { onDetach(entry.path); onAttach(next.path); }
              setPreview(null); await loadTree(currentPath);
            } catch (err) { onError(describeError(err)); }
          }}><Icon name="i-pencil" className="ico-sm" /></button>}
          {isDir ? null : (
            <span className="ws-acts">
              <button
                type="button"
                title={isAttached ? '取消附加' : '附加到对话'}
                aria-label={isAttached ? '取消附加' : '附加到对话'}
                onClick={() => toggleAttach(entry.path)}
              >
                <Icon name={isAttached ? 'i-check' : 'i-plus'} className="ico-sm" />
              </button>
              <button
                type="button"
                title="移入回收站"
                aria-label="移入回收站"
                disabled={busy || Boolean(deletingPath)}
                onClick={() => void removeFile(entry.path, entry.name)}
              >
                <Icon name="i-trash" className="ico-sm" />
              </button>
            </span>
          )}
        </div>
        {isDir ? <Collapse open={isOpen} className="ws-branch"><div className="ws-children">{isOpen || children[entry.path] ? renderChildren(entry.path) : null}</div></Collapse> : null}
      </div>
    );
  };

  const renderTrashRow = (item: WorkspaceTrashEntry): ReactNode => {
    const selected = item.operation_id === selectedTrashOperationId;
    const location = item.workspace_path || item.original_path;
    const select = () => setSelectedTrashOperationId(item.operation_id);
    return (
      <div className="ws-node" key={item.operation_id}>
        <div className="ws-row" aria-selected={selected}>
          <button
            type="button"
            className="ws-check"
            role="checkbox"
            aria-checked={selected}
            title="查看这一项"
            aria-label={`${item.name}：查看`}
            onClick={select}
          >
            <Icon name="i-check" className="ico-sm" />
          </button>
          <span className="ws-ico">
            <Icon name={item.item_type === 'directory' ? 'i-folder' : 'i-trashbin'} />
          </span>
          <button type="button" className="ws-open" title={location || item.name} onClick={select}>
            <span className="ws-name">{item.name}</span>
            <span className="ws-meta">{`删除于 ${timeText(item.deleted_at) || '较早前'}`}</span>
          </button>
          {item.exists_in_trash ? (
            <span className="ws-acts">
              <button
                type="button"
                title="恢复到原位置"
                aria-label="恢复到原位置"
                disabled={busy || Boolean(restoringOperationId)}
                onClick={() => void restoreItem(item)}
              >
                <Icon name="i-undo" className="ico-sm" />
              </button>
            </span>
          ) : null}
        </div>
      </div>
    );
  };

  const renderPreview = (): ReactNode => {
    if (loadingPreview && !preview) {
      return <p className="ws-empty">正在加载预览…</p>;
    }
    if (!preview) {
      return <p className="ws-empty">选择文件查看内容</p>;
    }
    const mime = preview.mime_type ?? '';
    const isImage = preview.is_binary && mime.startsWith('image/');
    const dirPath = parentPath(preview.path) ?? WORKSPACE_ROOT;
    const typeTitle = [preview.mime_type, preview.encoding].filter(Boolean).join(' · ');
    return (
      <div className="ws-preview" key={preview.path}>
        <div className="ws-preview-head">
          <span className="ws-preview-name" title={preview.path}>
            {preview.name}
          </span>
          <span className="meta">{timeText(preview.modified_at) || '—'}</span>
        </div>
        <dl className="ws-facts">
          <div className="ws-fact">
            <dt>类型</dt>
            <dd title={typeTitle || undefined}>{typeLabel(preview.name, preview.mime_type)}</dd>
          </div>
          <div className="ws-fact">
            <dt>大小</dt>
            <dd>{formatBytes(preview.size)}</dd>
          </div>
          <div className="ws-fact">
            <dt>目录</dt>
            <dd title={dirPath}>{dirPath === rootPath ? rootName : dirLabelFor(dirPath)}</dd>
          </div>
        </dl>
        <div className="ws-preview-body">
          {isImage ? (
            <img src={rawUrl} alt={preview.name} />
          ) : preview.is_binary ? (
            <p className="panel-hint">
              二进制文件不在这里渲染；可打开原文件，或附加给 Agent 用工具读取、转换。
            </p>
          ) : (
            <pre>{preview.content ?? ''}</pre>
          )}
        </div>
        {preview.truncated ? (
          <p className="panel-hint">预览已截断，仅显示前 {formatBytes(preview.max_bytes)}。</p>
        ) : null}
        <div className="ws-preview-acts">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => toggleAttach(preview.path)}
          >
            {attachedPaths.includes(preview.path) ? '移除附件' : '附加到对话'}
          </button>
          <a className="btn btn-quiet btn-sm" href={rawUrl} target="_blank" rel="noreferrer">
            <Icon name="i-external" className="ico-sm" />
            打开
          </a>
          <button
            type="button"
            className="btn btn-quiet btn-sm"
            disabled={busy || Boolean(deletingPath)}
            onClick={() => void removeFile(preview.path, preview.name)}
          >
            <Icon name="i-trash" className="ico-sm" />
            {deletingPath === preview.path ? '移入回收站中…' : '移入回收站'}
          </button>
        </div>
      </div>
    );
  };

  const renderTrashDetail = (): ReactNode => {
    if (loadingTrash && !trashItems.length) {
      return <p className="ws-empty">正在读取回收站…</p>;
    }
    if (!selectedTrashEntry) {
      return <p className="ws-empty">选择已删除文件查看详情</p>;
    }
    const location = selectedTrashEntry.workspace_path || selectedTrashEntry.original_path;
    const restoring = restoringOperationId === selectedTrashEntry.operation_id;
    return (
      <div className="ws-preview" key={selectedTrashEntry.operation_id}>
        <div className="ws-preview-head">
          <span className="ws-preview-name" title={location || selectedTrashEntry.name}>
            {selectedTrashEntry.name}
          </span>
          <span className="meta">{timeText(selectedTrashEntry.deleted_at) || '—'}</span>
        </div>
        <dl className="ws-detail">
          <dt>原始位置</dt>
          <dd title={location || undefined}>{location || '—'}</dd>
          <dt>回收站路径</dt>
          <dd title={selectedTrashEntry.trash_path || undefined}>
            {selectedTrashEntry.trash_path || '—'}
          </dd>
          <dt>状态</dt>
          <dd>{selectedTrashEntry.exists_in_trash ? '可恢复' : '文件缺失'}</dd>
        </dl>
        <div className="ws-preview-acts">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={Boolean(restoringOperationId) || !selectedTrashEntry.exists_in_trash}
            onClick={() => void restoreItem(selectedTrashEntry)}
          >
            <Icon name="i-undo" className="ico-sm" />
            {restoring ? '恢复中…' : '恢复到原位置'}
          </button>
        </div>
      </div>
    );
  };

  const renderList = (): ReactNode => {
    if (viewMode === 'workspace') {
      if (loadingTree && !entries.length) {
        return <p className="ws-empty">正在读取目录…</p>;
      }
      if (entries.length) {
        return entries.map((entry) => renderRow(entry));
      }
      return (
        <p className="ws-empty">
          <Icon name="i-folder" className="ico-lg" />
          <span>这个目录是空的</span>
        </p>
      );
    }
    if (loadingTrash && !trashItems.length) {
      return <p className="ws-empty">正在读取回收站…</p>;
    }
    if (trashItems.length) {
      return trashItems.map((item) => renderTrashRow(item));
    }
    return (
      <p className="ws-empty">
        <Icon name="i-trashbin" className="ico-lg" />
        <span>回收站为空</span>
      </p>
    );
  };

  return (
    <>
      <div className="workspace-manager-actions">
        <Dropdown value={rootPath} options={[{value: WORKSPACE_ROOT, label: '托管文件'}, ...roots.map(r => ({value: r.path, label: r.name + (r.available ? '' : '（不可用）')}))]} onChange={navigateTo} disabled={busy || uploading} ariaLabel="浏览目录" />
        <button className="btn btn-quiet btn-sm" disabled={busy} onClick={async () => {
          const values = await askForm({title: '新建子目录', fields: [{key: 'name', label: '目录名称', required: true}]});
          if (!values) return;
          if (/[\\/]/.test(values.name) || values.name === '.' || values.name === '..') { onError('请输入目录名称，不包含路径'); return; }
          try { await createWorkspaceDirectory(currentPath + '/' + values.name, workspaceId); await loadTree(currentPath); } catch (err) { onError(describeError(err)); }
        }}>新建目录</button>
      </div>
      <div className="row-between">
        <div className="segmented" role="group" aria-label="工作区视图">
          <button
            type="button"
            aria-pressed={viewMode === 'workspace'}
            onClick={() => switchView('workspace')}
          >
            文件
          </button>
          <button type="button" aria-pressed={viewMode === 'trash'} onClick={() => switchView('trash')}>
            回收站
          </button>
        </div>
        <button
          type="button"
          className="btn btn-quiet btn-icon btn-sm"
          title="刷新目录"
          aria-label="刷新目录"
          disabled={listBusy}
          onClick={refresh}
        >
          <Icon name="i-refresh" />
        </button>
      </div>

      {viewMode === 'trash' ? (
        <p className="panel-hint">删除只会移入回收站，可在这里恢复到原始位置。</p>
      ) : (
        <>
          <div className="ws-toolbar">
            <button
              type="button"
              className="btn btn-quiet btn-icon btn-sm"
              title="上级目录"
              aria-label="上级目录"
              disabled={!upPath}
              onClick={goParent}
            >
              <Icon name="i-up" />
            </button>
            <div className="pathbar" title={currentPath}>
              {crumbs.map((crumb, index) => (
                <Fragment key={crumb.path}>
                  {index ? <span className="sep">/</span> : null}
                  <button
                    type="button"
                    aria-current={index === crumbs.length - 1 ? 'true' : undefined}
                    onClick={() => navigateTo(crumb.path)}
                  >
                    {crumb.label}
                  </button>
                </Fragment>
              ))}
            </div>
          </div>
          <button
            type="button"
            className="ws-drop"
            data-active={dropActive ? 'true' : 'false'}
            disabled={uploading || busy}
            onClick={() => fileInputRef.current?.click()}
            onDragEnter={() => setDropActive(true)}
            onDragOver={onDragOverZone}
            onDragLeave={onDragLeaveZone}
            onDrop={onDropZone}
          >
            <Icon name="i-upload" className="ico-lg" />
            <span className="grow">
              <b>{uploading ? '上传中…' : '上传或拖入文件'}</b>
              加入「{currentPath === rootPath ? rootName : pathLabel(currentPath)}」，并自动附加到本次对话
            </span>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="sr-only"
            tabIndex={-1}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files ?? []);
              event.currentTarget.value = '';
              void uploadFiles(files);
            }}
          />
        </>
      )}

      <div className="ws-list" aria-busy={listBusy}>
        {renderList()}
      </div>

      {viewMode === 'workspace' ? renderPreview() : renderTrashDetail()}
    </>
  );
}
