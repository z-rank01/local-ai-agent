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
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (sessionToken) headers.set('X-Harness-Session', sessionToken);
  return fetch(input, {...init, headers, credentials: 'include'});
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public conversationId?: string, public code?: string) { super(message); }
}
export async function readJson<T>(response: Response): Promise<T> {
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
export async function requestJson<T>(path: string, init?: RequestInit, baseUrl = DEFAULT_BASE_URL): Promise<T> {
  return readJson<T>(await apiFetch(`${baseUrl}${path}`, init));
}
