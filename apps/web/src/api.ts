import type {
  AppStatus,
  Workspace,
  ChatRequest,
  ConversationSummary,
  MessageRecord,
  ModelInfo,
  PackageJob,
  ProviderInfo,
  StockModel,
  StockServiceResponse,
  UIStreamEvent,
  WorkspaceDeleteResponse,
  WorkspaceFilePreview,
  WorkspaceRestoreResponse,
  WorkspaceTrashResponse,
  WorkspaceTreeResponse,
  WorkspaceUploadResponse,
} from './types';

// Same-origin by default (BFF serves the production build); the Vite dev
// server on 5173 still talks to the BFF on 9510 unless overridden.
export const DEFAULT_BASE_URL =
  import.meta.env.VITE_LOCAL_AI_AGENT_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:9510' : '');

const SESSION_STORAGE_KEY = 'local-ai-agent.web-session';
function sessionCredential(): string {
  const token = new URLSearchParams(window.location.hash.slice(1)).get('session');
  try {
    if (token) { window.localStorage.setItem(SESSION_STORAGE_KEY, token); window.history.replaceState(null, '', window.location.pathname + window.location.search); }
    return token ?? window.localStorage.getItem(SESSION_STORAGE_KEY) ?? '';
  } catch { return token ?? ''; }
}
const sessionToken = sessionCredential();
async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (sessionToken) headers.set('X-Harness-Session', sessionToken);
  return fetch(input, {...init, headers, credentials: 'include'});
}
export async function ensureBrowserSession(): Promise<void> { await requestJson('/api/status'); }

export class ApiError extends Error {
  constructor(message: string, public status: number, public conversationId?: string, public code?: string) { super(message); }
}
async function readJson<T>(response: Response): Promise<T> {
  const raw = await response.text();
  let value: unknown;
  try { value = raw ? JSON.parse(raw) : null; } catch {
    throw new ApiError('后端返回了非预期内容，请确认服务是否正常运行。', response.status);
  }
  if (!response.ok) {
    const detail = (value as {detail?: unknown})?.detail;
    const structured = detail as {message?: string; conversation_id?: string; code?: string} | null;
    throw new ApiError(typeof detail === 'string' ? detail : structured?.message ?? `操作失败（HTTP ${response.status}）`, response.status, structured?.conversation_id, structured?.code);
  }
  return value as T;
}
async function requestJson<T>(path: string, init?: RequestInit, baseUrl = DEFAULT_BASE_URL): Promise<T> {
  return readJson<T>(await apiFetch(`${baseUrl}${path}`, init));
}
export function fetchWorkspaces(): Promise<Workspace[]> { return requestJson('/api/workspaces'); }

export function fetchRuns(): Promise<import('./types').RunList> { return requestJson('/api/runs'); }
export function fetchRun(id: string): Promise<import('./types').RunSummary> {
  return requestJson('/api/runs/'+encodeURIComponent(id));
}
export function controlRun(id: string, action: 'pause' | 'resume' | 'stop'): Promise<import('./types').RunSummary> {
  return requestJson('/api/runs/'+encodeURIComponent(id)+'/control', {
    method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({action}),
  });
}
export async function observeRun(id: string, after: number, onEvent: (event: UIStreamEvent) => void, signal: AbortSignal): Promise<void> {
  const response=await apiFetch(`${DEFAULT_BASE_URL}/api/runs/${encodeURIComponent(id)}/events?after_seq=${after}`, {signal});
  if (!response.ok) await readJson(response);
  if (!response.body) throw new Error('观察连接没有返回事件流');
  const reader=response.body.getReader(), decoder=new TextDecoder();
  let buffer='';
  try {
    while (true) {
      const {done,value}=await reader.read();
      buffer+=decoder.decode(value,{stream:!done});
      let end=buffer.indexOf('\n');
      while(end>=0) {
        const line=buffer.slice(0,end).trim(); buffer=buffer.slice(end+1);
        if(line) onEvent(JSON.parse(line));
        end=buffer.indexOf('\n');
      }
      if(done) { if(buffer.trim()) onEvent(JSON.parse(buffer)); return; }
    }
  } finally { reader.releaseLock(); }
}

export function deliverWorkspaceBrief(conversationId: string, path: string): Promise<MessageRecord> {
  return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/brief-delivery`, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({path}),
  });
}

export function fetchReportPage(conversationId: string, messageId: string, page: number,
  section?: import('./lib/reportDocument').ReportSection, revision?: string):
  Promise<import('./lib/reportDocument').ReportPage> {
  return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/report-page`, {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({message_id: messageId, page, request_id: crypto.randomUUID(), section, revision}),
  });
}
export function saveWorkspace(body: Record<string, unknown>, id?: string): Promise<Workspace> {
  if (Array.isArray(body.roots)) body = {...body, roots: body.roots.map(root => {const r = root as {host_path: string; name?: string; id?: string}; return {host_path: r.host_path, name: r.name, id: r.id};})};
  return requestJson('/api/workspaces' + (id ? '/' + encodeURIComponent(id) : ''), {
    method: id ? 'PATCH' : 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body),
  });
}
export function activateWorkspace(id: string): Promise<Workspace> {
  return requestJson(`/api/workspaces/${encodeURIComponent(id)}/activate`, {method: 'POST'});
}
export async function removeWorkspace(id: string): Promise<void> {
  const response = await apiFetch(`${DEFAULT_BASE_URL}/api/workspaces/${encodeURIComponent(id)}`, {method: 'DELETE'});
  if (!response.ok) await readJson(response);
}
export function createWorkspaceDirectory(path: string, workspaceId: string): Promise<{path: string}> {
  return requestJson(`/api/workspace/directory?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({path}),
  });
}
export function renameWorkspaceFile(src: string, dst: string, workspaceId: string): Promise<{path: string}> {
  return requestJson(`/api/workspace/rename?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({src, dst}),
  });
}

export function fetchStatus(baseUrl = DEFAULT_BASE_URL, workspaceId = 'default'): Promise<AppStatus> {
  return requestJson<AppStatus>(`/api/status?workspace_id=${encodeURIComponent(workspaceId)}`, undefined, baseUrl);
}

export function fetchModels(refresh = false, baseUrl = DEFAULT_BASE_URL): Promise<ModelInfo[]> {
  return requestJson<ModelInfo[]>(`/api/models${refresh ? '?refresh=1' : ''}`, undefined, baseUrl);
}

export function fetchProviders(refresh = false, baseUrl = DEFAULT_BASE_URL): Promise<ProviderInfo[]> {
  return requestJson<ProviderInfo[]>(`/api/providers${refresh ? '?refresh=1' : ''}`, undefined, baseUrl);
}

export function saveProviderCredential(
  providerId: string,
  apiKey: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<{status: string}> {
  return requestJson<{status: string}>(
    `/api/providers/${encodeURIComponent(providerId)}/credential`,
    {
      method: 'PUT',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({api_key: apiKey}),
    },
    baseUrl,
  );
}

export function patchModelSettings(
  body: {
    model_id: string;
    workspace_path: string;
    workspace_id?: string;
    thinking_enabled?: boolean | null;
    thinking_budget?: number | null;
    workspace_cloud_allowed?: boolean;
    workspace_cloud_write_allowed?: boolean;
  },
  baseUrl = DEFAULT_BASE_URL,
): Promise<{status: string}> {
  return requestJson<{status: string}>(
    '/api/model-settings',
    {
      method: 'PATCH',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(body),
    },
    baseUrl,
  );
}

export async function shutdownStack(baseUrl = DEFAULT_BASE_URL): Promise<{stopping: Record<string, number[]>}> {
  const response = await apiFetch(`${baseUrl}/api/admin/shutdown-stack`, {method: 'POST'});
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`stack shutdown failed: ${response.status}${detail ? ` ${detail}` : ''}`);
  }
  return (await response.json()) as {stopping: Record<string, number[]>};
}

export function sendHeartbeat(baseUrl = DEFAULT_BASE_URL): Promise<{ok: boolean; auto_exit: boolean}> {
  return requestJson<{ok: boolean; auto_exit: boolean}>('/api/heartbeat', {method: 'POST'}, baseUrl);
}

export type SkillsStatus = {websearch: {enabled: boolean; active: boolean; url: string}};

export type ResearchMethod = {id: string; title: string; roles: string[]; source: string; source_version: string; version: string};

export function fetchResearchMethods(baseUrl = DEFAULT_BASE_URL): Promise<{methods: ResearchMethod[]}> {
  return requestJson<{methods: ResearchMethod[]}>('/api/research-methods', undefined, baseUrl);
}

export function updateResearchMethod(conversationId: string, methodId: string,
  baseUrl = DEFAULT_BASE_URL): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>(`/api/conversations/${conversationId}/research-method`, {
    method: 'PUT', headers: {'content-type': 'application/json'},
    body: JSON.stringify({method_id: methodId}),
  }, baseUrl);
}

export function fetchSkills(baseUrl = DEFAULT_BASE_URL): Promise<SkillsStatus> {
  return requestJson<SkillsStatus>('/api/admin/skills', undefined, baseUrl);
}

export async function setWebsearch(enabled: boolean, baseUrl = DEFAULT_BASE_URL): Promise<SkillsStatus> {
  return requestJson<SkillsStatus>('/api/admin/skills', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({websearch: enabled}),
  }, baseUrl);
}

export function fetchConversations(
  query?: string,
  baseUrl = DEFAULT_BASE_URL,
  workspaceId?: string,
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams();
  if (query?.trim()) {
    params.set('query', query.trim());
  }
  if (workspaceId) params.set('workspace_id', workspaceId);
  const suffix = params.size ? `?${params.toString()}` : '';
  return requestJson<ConversationSummary[]>(`/api/conversations${suffix}`, undefined, baseUrl);
}

export function fetchConversation(id: string): Promise<ConversationSummary> {
  return requestJson(`/api/conversations/${encodeURIComponent(id)}`);
}

export function updateConversationTitle(
  conversationId: string,
  title: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>(
    `/api/conversations/${conversationId}`,
    {
      method: 'PATCH',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({title}),
    },
    baseUrl,
  );
}

export function fetchMessages(
  conversationId: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<MessageRecord[]> {
  return requestJson<MessageRecord[]>(`/api/conversations/${conversationId}/messages`, undefined, baseUrl);
}

export function updateConversationModel(conversationId: string, model: string): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>(`/api/conversations/${conversationId}`, {
    method: 'PATCH', headers: {'content-type':'application/json'}, body: JSON.stringify({model}),
  });
}

export async function deleteConversation(
  conversationId: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<void> {
  const response = await apiFetch(`${baseUrl}/api/conversations/${conversationId}`, {method: 'DELETE'});
  if (!response.ok) {
    throw new Error(`conversation delete failed: ${response.status}`);
  }
}

export async function deleteMessage(
  conversationId: string,
  messageId: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<void> {
  const response = await apiFetch(
    `${baseUrl}/api/conversations/${conversationId}/messages/${messageId}`,
    {method: 'DELETE'},
  );
  if (!response.ok) {
    throw new Error(`message delete failed: ${response.status}`);
  }
}

export function activateMessageVersion(
  conversationId: string,
  messageId: string,
  versionNumber: number,
  baseUrl = DEFAULT_BASE_URL,
): Promise<MessageRecord[]> {
  return requestJson<MessageRecord[]>(
    `/api/conversations/${conversationId}/messages/${messageId}/activate-version`,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({version_number: versionNumber}),
    },
    baseUrl,
  );
}

export function exportConversationUrl(
  conversationId: string,
  format = 'markdown',
  baseUrl = DEFAULT_BASE_URL,
): string {
  return `${baseUrl}/api/conversations/${conversationId}/export?format=${encodeURIComponent(format)}`;
}

export async function downloadConversation(
  conversationId: string,
  format: 'markdown' | 'json' | 'txt' = 'markdown',
  baseUrl = DEFAULT_BASE_URL,
): Promise<{filename: string; blob: Blob}> {
  const response = await apiFetch(exportConversationUrl(conversationId, format, baseUrl));
  if (!response.ok) {
    throw new Error(`conversation export failed: ${response.status}`);
  }
  const disposition = response.headers.get('content-disposition') ?? '';
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const fallbackExt = format === 'json' ? 'json' : format === 'txt' ? 'txt' : 'md';
  const filename = match ? match[1] : `${conversationId}.${fallbackExt}`;
  return {filename, blob: await response.blob()};
}

export function fetchWorkspaceTree(
  path = '/workspace',
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceTreeResponse> {
  return requestJson<WorkspaceTreeResponse>(
    `/api/workspace/tree?path=${encodeURIComponent(path)}&workspace_id=${encodeURIComponent(workspaceId)}`,
    undefined,
    baseUrl,
  );
}

export function fetchWorkspacePreview(
  path: string,
  maxBytes = 200_000,
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceFilePreview> {
  return requestJson<WorkspaceFilePreview>(
    `/api/workspace/preview?path=${encodeURIComponent(path)}&max_bytes=${maxBytes}&workspace_id=${encodeURIComponent(workspaceId)}`,
    undefined,
    baseUrl,
  );
}

export async function uploadWorkspaceFile(
  file: File,
  targetDir: string,
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceUploadResponse> {
  const params = new URLSearchParams({filename: file.name, target_dir: targetDir, workspace_id: workspaceId});
  const response = await apiFetch(`${baseUrl}/api/workspace/upload?${params.toString()}`, {
    method: 'POST',
    headers: {'content-type': file.type || 'application/octet-stream'},
    body: file,
  });
  if (!response.ok) {
    await readJson(response);
  }
  return (await response.json()) as WorkspaceUploadResponse;
}

export async function deleteWorkspaceFile(
  path: string,
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceDeleteResponse> {
  const response = await apiFetch(`${baseUrl}/api/workspace/file?path=${encodeURIComponent(path)}&workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    await readJson(response);
  }
  return (await response.json()) as WorkspaceDeleteResponse;
}

export function fetchWorkspaceTrash(
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceTrashResponse> {
  return requestJson<WorkspaceTrashResponse>(`/api/workspace/trash?workspace_id=${encodeURIComponent(workspaceId)}`, undefined, baseUrl);
}

export async function restoreWorkspaceTrashItem(
  operationId: string,
  workspaceId = 'default',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceRestoreResponse> {
  const response = await apiFetch(
    `${baseUrl}/api/workspace/trash/${encodeURIComponent(operationId)}/restore?workspace_id=${encodeURIComponent(workspaceId)}`,
    {method: 'POST'},
  );
  if (!response.ok) {
    await readJson(response);
  }
  return (await response.json()) as WorkspaceRestoreResponse;
}

export function workspaceRawUrl(path: string, workspaceId = 'default', baseUrl = DEFAULT_BASE_URL): string {
  return `${baseUrl}/api/workspace/raw?path=${encodeURIComponent(path)}&workspace_id=${encodeURIComponent(workspaceId)}`;
}

export function fetchPackageJobs(signal?: AbortSignal, baseUrl = DEFAULT_BASE_URL, workspaceId = 'default'): Promise<PackageJob[]> {
  return requestJson<PackageJob[]>(`/api/package-jobs?workspace_id=${encodeURIComponent(workspaceId)}`, {signal}, baseUrl);
}

export function cancelPackageJob(jobId: string, baseUrl = DEFAULT_BASE_URL, workspaceId = 'default'): Promise<PackageJob> {
  return requestJson<PackageJob>(
    `/api/package-jobs/${encodeURIComponent(jobId)}/cancel?workspace_id=${encodeURIComponent(workspaceId)}`,
    {method: 'POST'},
    baseUrl,
  );
}

// Stock-service replies are read from the raw body: a failed call carries its
// reason in `detail`, and a successful action may return only `challenge`
// (one-off confirmation) or `reply` (operator answer) instead of a status
// payload — those must be passed through untouched.
async function readStockServiceResponse(response: Response): Promise<StockServiceResponse> {
  const raw = await response.text();
  let value: unknown;
  try {
    value = raw ? JSON.parse(raw) : null;
  } catch {
    value = {detail: raw};
  }
  if (!response.ok) {
    const detail = typeof value === 'object' && value !== null && 'detail' in value && typeof value.detail === 'string'
      ? value.detail
      : `服务操作失败（HTTP ${response.status}）`;
    throw new Error(detail);
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('股票服务返回了无效响应');
  }
  return value as StockServiceResponse;
}

export async function fetchStockService(baseUrl = DEFAULT_BASE_URL): Promise<StockServiceResponse> {
  const response = await apiFetch(`${baseUrl}/api/admin/stock-service`);
  return readStockServiceResponse(response);
}

export async function stockServiceAction(
  body: Record<string, unknown>,
  baseUrl = DEFAULT_BASE_URL,
): Promise<StockServiceResponse> {
  const response = await apiFetch(`${baseUrl}/api/admin/stock-service`, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body),
  });
  return readStockServiceResponse(response);
}

export async function fetchStockModels(refresh = false, baseUrl = DEFAULT_BASE_URL): Promise<StockModel[]> {
  const response = await apiFetch(`${baseUrl}/api/admin/stock-models${refresh ? '?refresh=1' : ''}`);
  if (!response.ok) {
    throw new Error(`/api/admin/stock-models failed: ${response.status}`);
  }
  const value: unknown = await response.json();
  if (!Array.isArray(value)) {
    throw new Error('简报分析模型列表格式无效');
  }
  return value as StockModel[];
}

// Signature order is deliberate: streamChat(request, onEvent, options) takes
// the event callback second, while streamRegenerate(id, options, onEvent) and
// streamEditMessage(id, messageId, content, options, onEvent) take it last.
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: UIStreamEvent) => void,
  options: {signal?: AbortSignal; baseUrl?: string} = {},
): Promise<void> {
  const response = await apiFetch(`${options.baseUrl ?? DEFAULT_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(request),
    signal: options.signal,
  });

  if (!response.ok) await readJson(response);
  if (!response.body) throw new Error('后端没有返回输出流');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, {stream: true});
    let newlineIndex = buffer.indexOf('\n');
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        onEvent(JSON.parse(line) as UIStreamEvent);
      }
      newlineIndex = buffer.indexOf('\n');
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as UIStreamEvent);
  }
}

export async function streamRegenerate(
  conversationId: string,
  options: {providerId?: string; model?: string; messageId?: string | null; requestId?: string; signal?: AbortSignal; baseUrl?: string},
  onEvent: (event: UIStreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(
    `${options.baseUrl ?? DEFAULT_BASE_URL}/api/conversations/${conversationId}/regenerate`,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({message_id: options.messageId ?? null, provider_id: options.providerId, model: options.model, request_id: options.requestId}),
      signal: options.signal,
    },
  );

  if (!response.ok) await readJson(response);
  if (!response.body) throw new Error('后端没有返回输出流');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, {stream: true});
    let newlineIndex = buffer.indexOf('\n');
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        onEvent(JSON.parse(line) as UIStreamEvent);
      }
      newlineIndex = buffer.indexOf('\n');
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as UIStreamEvent);
  }
}

export async function streamEditMessage(
  conversationId: string,
  messageId: string,
  content: string,
  options: {providerId?: string; model?: string; requestId?: string; signal?: AbortSignal; baseUrl?: string} = {},
  onEvent: (event: UIStreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(
    `${options.baseUrl ?? DEFAULT_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/edit`,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({content, provider_id: options.providerId, model: options.model, request_id: options.requestId}),
      signal: options.signal,
    },
  );

  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => '');
    throw new Error(`edit message failed: ${response.status}${detail ? ` ${detail}` : ''}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, {stream: true});
    let newlineIndex = buffer.indexOf('\n');
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        onEvent(JSON.parse(line) as UIStreamEvent);
      }
      newlineIndex = buffer.indexOf('\n');
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as UIStreamEvent);
  }
}
