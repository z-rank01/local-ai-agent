import {DEFAULT_BASE_URL, apiFetch, requestJson} from '../../extensions/http';
import type {ConversationSummary} from '../../types';
import type {StockModel, StockServiceResponse} from './types';

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

