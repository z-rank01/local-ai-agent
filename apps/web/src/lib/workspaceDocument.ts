import type {WorkspaceFilePreview} from '../types';
import type {ReadingReport} from './report';
import {prepareHtmlBrief} from './htmlBrief';

export type WorkspaceDocumentKind = 'html' | 'markdown';
export type WorkspaceDocumentInfo = ReadingReport & {
  kind: WorkspaceDocumentKind;
  path: string;
  meta: string[];
};

export function workspaceDocumentKey(path: string): string {
  let normalized = path;
  try {
    normalized = decodeURIComponent(path);
  } catch {
    return '';
  }
  if (!/^\/(workspace|roots\/[^/]+)\/.+/.test(normalized) || /[?#\\]/.test(normalized)) {
    return '';
  }
  if (normalized.split('/').some(part => part === '.' || part === '..')) {
    return '';
  }
  return normalized;
}

export function workspaceDocumentKind(path: string): WorkspaceDocumentKind | null {
  const normalized = workspaceDocumentKey(path);
  if (!normalized) return null;
  if (/\.html?$/i.test(normalized)) return 'html';
  if (/\.(md|markdown)$/i.test(normalized)) return 'markdown';
  return null;
}

export function prepareWorkspaceDocument(
  file: WorkspaceFilePreview,
  workspaceId: string,
): WorkspaceDocumentInfo {
  const path = workspaceDocumentKey(file.path);
  const kind = workspaceDocumentKind(file.path);
  if (!path || !kind || file.is_binary || file.truncated || !file.content) {
    throw new Error('文档未完整读取，请从工作区下载原文件。');
  }
  if (kind === 'html') {
    const htmlBrief = prepareHtmlBrief(file, workspaceId);
    return {kind, path, title: htmlBrief.title, content: '', htmlBrief, meta: htmlBrief.meta};
  }
  return {kind, path, title: file.name, content: file.content, meta: []};
}
