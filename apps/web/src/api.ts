import {DEFAULT_BASE_URL, ApiError, apiFetch, readJson, requestJson} from './extensions/http';
import type {SavedArtifact} from './extensions/contracts';
import type {
  AppStatus,
  Workspace,
  ChatRequest,
  ConversationSummary,
  MessageRecord,
  ModelInfo,
  PackageJob,
  ProviderInfo,
  UIStreamEvent,
  WorkspaceDeleteResponse,
  WorkspaceFilePreview,
  WorkspaceRestoreResponse,
  WorkspaceTrashResponse,
  WorkspaceTreeResponse,
  WorkspaceUploadResponse,
} from './types';

export type CapabilitySummary = {
  id: string; kind: 'plugin'; title?: string; description?: string; version?: string;
  registration: 'registered' | 'unregistered' | 'invalid';
  required?: string[]; optional?: string[]; permissions?: string[];
  tools: string[]; error: string | null;
  lifecycle?: Record<string, {state: string; error: string; inflight: number; accepting: boolean}>;
  management?: 'managed' | 'managed_service' | 'legacy_adapter';
  origin?: 'builtin' | 'local'; scope?: 'host';
  actions?: {toggle: boolean; uninstall: boolean; edit: boolean}; action_note?: string;
  environment_required?: boolean;
  environment?: {state: string; error?: string};
};

export function prepareCapability(identity: string): Promise<unknown> {
  return requestJson(`/api/capabilities/${encodeURIComponent(identity)}/prepare`, {method:'POST'});
}

export function setCapability(identity: string, enabled: boolean): Promise<unknown> {
  return requestJson(`/api/capabilities/${encodeURIComponent(identity)}`, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({enabled})});
}

export function fetchCapabilities(): Promise<{api_version: number; capabilities: CapabilitySummary[]}> {
  return requestJson('/api/capabilities');
}

export type SkillRevision = {name: string; description: string; version?: string; revision: string;
  permissions: string[]; dependencies: string[]; required?: string[]; optional?: string[];
  entrypoints?: {path: string; kind: string}[]; environment_dependencies?: string[]; kind?: string; path: string};
export type ManagedSkill = {id: string; enabled: boolean; missing?: boolean; uninstalled?: boolean; candidate_error?: string;
  active: SkillRevision | null; pending?: SkillRevision | null; history: SkillRevision[]; blockers: string[]};
export type SkillDiff = {files: {path: string; change: string; before_bytes: number; after_bytes: number;
  diff?: string; truncated?: boolean; preview?: string}[]};
export function fetchSkillDiff(workspaceId: string, name: string, after: string): Promise<SkillDiff> {
  return requestJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/skills/${encodeURIComponent(name)}/diff?after=${encodeURIComponent(after)}`);
}
export function fetchManagedSkills(workspaceId: string): Promise<{skills: ManagedSkill[]; invalid: {name: string; error: string}[]}> {
  return requestJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/skills`);
}
export function changeManagedSkill(workspaceId: string, name: string, action: string, revision?: string): Promise<ManagedSkill> {
  return requestJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/skills/${encodeURIComponent(name)}`,
    {method: 'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({action, revision})});
}
export function importManagedSkill(workspaceId: string, path: string): Promise<{name: string}> {
  return requestJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/skills-import`,
    {method: 'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({path})});
}

export type SkillSourceFile = {path: string; size: number; sha256: string | null; writable: boolean; reason: string};
export type SkillSourceText = SkillSourceFile & {content: string | null; revision?: string; activation?: string};
const skillSourceUrl = (workspaceId: string, name: string) =>
  `/api/workspaces/${encodeURIComponent(workspaceId)}/skills/${encodeURIComponent(name)}`;
export const fetchSkillSourceFiles = (workspaceId: string, name: string) =>
  requestJson<{files: SkillSourceFile[]}>(skillSourceUrl(workspaceId, name) + '/source');
export const fetchSkillSourceFile = (workspaceId: string, name: string, path: string) =>
  requestJson<SkillSourceText>(skillSourceUrl(workspaceId, name) + '/source-file?path=' + encodeURIComponent(path));
export const saveSkillSourceFile = (workspaceId: string, name: string, path: string, content: string, sha256: string) =>
  requestJson<SkillSourceText>(skillSourceUrl(workspaceId, name) + '/source-file?path=' + encodeURIComponent(path), {
    method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify({content, sha256}),
  });

// Same-origin by default (BFF serves the production build); the Vite dev
// server on 5173 still talks to the BFF on 9510 unless overridden.
export {DEFAULT_BASE_URL, ApiError} from './extensions/http';
export async function ensureBrowserSession(): Promise<void> { await requestJson('/api/status'); }
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

export function fetchSavedArtifact(conversationId: string, messageId: string): Promise<SavedArtifact> {
  return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/artifacts/${encodeURIComponent(messageId)}`);
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

export function updateConversationCapabilities(
  conversationId: string,
  capabilityIds: string[],
): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({capability_ids: capabilityIds}),
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
  options: {providerId?: string; model?: string; messageId?: string | null; requestId?: string; capabilityIds?: string[]; signal?: AbortSignal; baseUrl?: string},
  onEvent: (event: UIStreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(
    `${options.baseUrl ?? DEFAULT_BASE_URL}/api/conversations/${conversationId}/regenerate`,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({message_id: options.messageId ?? null, provider_id: options.providerId, model: options.model, request_id: options.requestId, capability_ids: options.capabilityIds}),
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
  options: {providerId?: string; model?: string; requestId?: string; capabilityIds?: string[]; signal?: AbortSignal; baseUrl?: string} = {},
  onEvent: (event: UIStreamEvent) => void,
): Promise<void> {
  const response = await apiFetch(
    `${options.baseUrl ?? DEFAULT_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/edit`,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({content, provider_id: options.providerId, model: options.model, request_id: options.requestId, capability_ids: options.capabilityIds}),
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
