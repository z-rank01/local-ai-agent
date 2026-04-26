import type {
  AppStatus,
  ChatRequest,
  ConversationSummary,
  MessageRecord,
  ModelInfo,
  ProviderInfo,
  UIStreamEvent,
  WorkspaceFilePreview,
  WorkspaceTreeResponse,
  WorkspaceUploadResponse,
} from './types';

export const DEFAULT_BASE_URL =
  import.meta.env.VITE_LOCAL_AI_AGENT_API_URL ?? 'http://127.0.0.1:9510';

async function requestJson<T>(path: string, init?: RequestInit, baseUrl = DEFAULT_BASE_URL): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init);
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`${path} failed: ${response.status}${detail ? ` ${detail}` : ''}`);
  }
  return (await response.json()) as T;
}

export function fetchStatus(baseUrl = DEFAULT_BASE_URL): Promise<AppStatus> {
  return requestJson<AppStatus>('/api/status', undefined, baseUrl);
}

export function fetchModels(baseUrl = DEFAULT_BASE_URL): Promise<ModelInfo[]> {
  return requestJson<ModelInfo[]>('/api/models', undefined, baseUrl);
}

export function fetchProviders(baseUrl = DEFAULT_BASE_URL): Promise<ProviderInfo[]> {
  return requestJson<ProviderInfo[]>('/api/providers', undefined, baseUrl);
}

export function fetchConversations(baseUrl = DEFAULT_BASE_URL): Promise<ConversationSummary[]> {
  return requestJson<ConversationSummary[]>('/api/conversations', undefined, baseUrl);
}

export function createConversation(
  title = '新对话',
  model?: string | null,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ConversationSummary> {
  return requestJson<ConversationSummary>(
    '/api/conversations',
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({title, model}),
    },
    baseUrl,
  );
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

export async function deleteConversation(
  conversationId: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/conversations/${conversationId}`, {method: 'DELETE'});
  if (!response.ok) {
    throw new Error(`conversation delete failed: ${response.status}`);
  }
}

export function fetchWorkspaceTree(
  path = '/workspace',
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceTreeResponse> {
  return requestJson<WorkspaceTreeResponse>(
    `/api/workspace/tree?path=${encodeURIComponent(path)}`,
    undefined,
    baseUrl,
  );
}

export function fetchWorkspacePreview(
  path: string,
  maxBytes = 200_000,
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceFilePreview> {
  return requestJson<WorkspaceFilePreview>(
    `/api/workspace/preview?path=${encodeURIComponent(path)}&max_bytes=${maxBytes}`,
    undefined,
    baseUrl,
  );
}

export async function uploadWorkspaceFile(
  file: File,
  targetDir: string,
  baseUrl = DEFAULT_BASE_URL,
): Promise<WorkspaceUploadResponse> {
  const params = new URLSearchParams({filename: file.name, target_dir: targetDir});
  const response = await fetch(`${baseUrl}/api/workspace/upload?${params.toString()}`, {
    method: 'POST',
    headers: {'content-type': file.type || 'application/octet-stream'},
    body: file,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`workspace upload failed: ${response.status}${detail ? ` ${detail}` : ''}`);
  }
  return (await response.json()) as WorkspaceUploadResponse;
}

export function workspaceRawUrl(path: string, baseUrl = DEFAULT_BASE_URL): string {
  return `${baseUrl}/api/workspace/raw?path=${encodeURIComponent(path)}`;
}

export async function streamChat(
  request: ChatRequest,
  onEvent: (event: UIStreamEvent) => void,
  options: {signal?: AbortSignal; baseUrl?: string} = {},
): Promise<void> {
  const response = await fetch(`${options.baseUrl ?? DEFAULT_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(request),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`chat stream failed: ${response.status}`);
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
