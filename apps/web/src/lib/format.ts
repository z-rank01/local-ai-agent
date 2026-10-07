import {createElement, type ReactNode} from 'react';
import type {TranscriptBlock, UIStreamEvent} from '../types';

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function formatTime(value?: string): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

export function formatBackendError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return 'Python BFF 未连接。请先启动后端，或稍后点击“重新连接”。';
  }
  if (err instanceof SyntaxError || /is not valid json|unexpected token/i.test(raw)) {
    return '后端返回了非预期内容，请确认服务是否正常运行。';
  }
  return raw;
}

export function eventText(event: UIStreamEvent, key: string): string {
  const value = event.data[key];
  return typeof value === 'string' ? value : '';
}

export function formatToolElapsed(value?: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '';
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)}s`;
}

export function formatJsonText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
    return text;
  }
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return text;
  }
}

export function formatToolParamValue(value: unknown): string {
  if (typeof value === 'string') {
    return formatJsonText(value);
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (value == null) {
    return 'null';
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function looksStructuredToolOutput(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[');
}

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function splitStoredToolContent(text: string): {headline: string; detail: string} {
  const normalized = text.replace(/^\[(ok|error)\]\s*/i, '').trim();
  const newlineIndex = normalized.indexOf('\n');
  if (newlineIndex < 0) {
    return {headline: normalized, detail: normalized};
  }
  const headline = normalized.slice(0, newlineIndex).trim();
  const detail = normalized.slice(newlineIndex + 1).trim();
  return {headline, detail: detail || headline};
}

export function parseToolJson<T>(text: string): T | null {
  const detail = splitStoredToolContent(text).detail;
  if (!looksStructuredToolOutput(detail)) {
    return null;
  }
  try {
    return JSON.parse(detail) as T;
  } catch {
    return null;
  }
}

export function structuredToolPayload<T>(block: TranscriptBlock): T | null {
  if (block.toolResult !== undefined && block.toolResult !== null) {
    return block.toolResult as T;
  }
  return parseToolJson<T>(block.text);
}

export function roleLabel(role?: string | null): string {
  if (role === 'user') {
    return '用户命中';
  }
  if (role === 'assistant') {
    return '回答命中';
  }
  if (role === 'tool') {
    return '工具命中';
  }
  return '内容命中';
}

export function highlightText(text: string, query: string): ReactNode {
  const needle = query.trim();
  if (!needle) {
    return text;
  }
  const pattern = new RegExp(escapeRegExp(needle), 'ig');
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(createElement('mark', {key: `${match.index}-${match[0]}`}, match[0]));
    lastIndex = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length ? parts : text;
}

export async function copyText(text: string): Promise<boolean> {
  if (!text) {
    return false;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

export function formatBytes(value?: number | null): string {
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

export function parentPath(path: string): string | null {
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

export function pathLabel(path: string): string {
  return path.replace('/workspace', 'workspace') || 'workspace';
}
