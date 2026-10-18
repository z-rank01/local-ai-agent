export type Workspace = {
  id: string; name: string; version: number; managed_path: string; primary_path: string;
  roots: Array<{id: string; name: string; host_path: string; path: string; available: boolean}>;
  primary_root_id: string | null; cloud_read: boolean; cloud_write: boolean;
  active_conversation_id: string | null; pending: boolean; last_error?: string;
};

export type AppStatus = {
  workspace_id: string;
  tool_catalog?: Array<{name: string; display_name: string; group: string}>;
  model_calls_used?: number;
  model_call_limit?: number;
  workspace_cloud_allowed?: boolean;
  cloud_write_allowed?: boolean;
  status: string;
  model: string;
  workspace_path: string;
  tools: string[];
  websearch_enabled: boolean;
};

export type ConversationSummary = {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  research_method: string;
  workspace_id: string;
  workspace_name: string;
  search_snippet?: string | null;
};

export type MessageRecord = {
  file_delivery?: boolean;
  model?: string;
  status?: string;
  params?: Record<string, unknown>;
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  thinking: string;
  tool_calls: Array<Record<string, unknown>>;
  tool_name: string;
  tool_result?: unknown;
  response_to_message_id: string;
  version_number: number;
  version_count: number;
  active: boolean;
  created_at: string;
};

export type ModelInfo = {
  thinking_supported: boolean;
  thinking_budget_supported: boolean;
  thinking_enabled: boolean | null;
  thinking_budget: number | null;
  id: string;
  name: string;
  provider_id: string;
  provider_name: string;
  default: boolean;
  capabilities: string[];
  context_window?: number | null;
  context_capacity_source?: string | null;
  status: string;
};

export type ProviderInfo = {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  base_url?: string | null;
  models: ModelInfo[];
};

export type ChatRequest = {
  workspace_id?: string;
  request_id?: string;
  message: string;
  conversation_id?: string | null;
  title?: string | null;
  provider_id?: string | null;
  model?: string | null;
  options?: Record<string, unknown>;
};

export type ImportedAttachment = {
  source_path: string;
  local_path: string;
  workspace_path: string;
  display_name: string;
};

export type WorkspaceEntry = {
  name: string;
  path: string;
  kind: 'file' | 'directory';
  size?: number | null;
  modified_at?: string | null;
  mime_type?: string | null;
};

export type WorkspaceTreeResponse = {
  root: string;
  entries: WorkspaceEntry[];
};

export type WorkspaceFilePreview = {
  name: string;
  path: string;
  size: number;
  modified_at?: string | null;
  mime_type?: string | null;
  encoding?: string | null;
  content?: string | null;
  is_binary: boolean;
  truncated: boolean;
  max_bytes: number;
};

export type WorkspaceUploadResponse = {
  attachment: ImportedAttachment;
  entry: WorkspaceEntry;
};

export type WorkspaceDeleteResponse = {
  deleted_path: string;
  moved_to_trash: string;
  operation_id?: string | null;
  manifest?: string | null;
};

export type WorkspaceTrashEntry = {
  operation_id: string;
  deleted_at?: string | null;
  name: string;
  relative_path: string;
  workspace_path: string;
  original_path: string;
  trash_path: string;
  item_type: 'file' | 'directory';
  exists_in_trash: boolean;
};

export type WorkspaceTrashResponse = {
  items: WorkspaceTrashEntry[];
};

export type WorkspaceRestoreResponse = {
  operation_id: string;
  restored_to: string;
  workspace_path: string;
  deleted_at?: string | null;
};

export type UIStreamEvent = {
  seq?: number | null;
  event: string;
  conversation_id?: string | null;
  run_id?: string | null;
  block_id?: string | null;
  message_id?: string | null;
  data: Record<string, unknown>;
};

export type RunSummary = {
  id: string; source?: 'stock'; kind: string; conversation_id: string | null; workspace_id: string;
  request_id: string; title: string; model: string; status: string; phase: string;
  created_at: string; updated_at: string; last_seq: number; error?: string | null;
  resumable: boolean; resume_blocker: string; checkpoint_available: boolean;
  business_status?: string; report_available?: boolean; actions?: string[];
};
export type RunList = {runs: RunSummary[]; stock_service: {online: boolean; message?: string;
  worker?: {enabled: boolean; busy: boolean; research_only: boolean}}};

export type TranscriptKind = 'user' | 'assistant' | 'reasoning' | 'tool' | 'meta' | 'error';

export type TranscriptBlock = {
  fileDelivery?: boolean;
  id: string;
  kind: TranscriptKind;
  label: string;
  text: string;
  summary?: string;
  messageId?: string | null;
  status?: 'running' | 'ok' | 'error';
  collapsible?: boolean;
  collapsed?: boolean;
  placeholder?: boolean;
  params?: Record<string, unknown>;
  toolResult?: unknown;
  elapsed?: number;
  createdAt?: string;
  responseToMessageId?: string;
  versionNumber?: number;
  versionCount?: number;
};

export type PackageJob = {
  job_id: string;
  status: string;
  packages: string[];
  skipped: string[];
  elapsed: number;
  log: string;
  error?: string;
  completed: boolean;
};

export type StockServiceStatus = {
  online: boolean;
  message?: string;
  market_model?: {provider: string; model: string};
  research_model?: {enabled: boolean; provider: string; model: string; duties: string[]};
  settings: {root: string; state_dir: string; port: number; enabled: boolean; worker: boolean};
  worker?: {enabled: boolean; draining: boolean; busy: boolean; research_only?: boolean};
  runtime?: {mode: string; reason: string; last_completed_day: string | null};
  recovery?: {
    batch: {status: string; from_day: string; to_day: string} | null;
    steps: {id: string; status: string; body: {day?: string; error?: string; attempts?: number}}[];
    problems: string[];
    human_tasks: {id: string; kind: string; body: {message?: string}}[];
  };
  position_reviews?: {
    id: string;
    day: string;
    status: string;
    symbol: string;
    reasons: string[];
    review: {status?: string; verdict?: string | null; report?: string; missing?: string[]; decision_id?: string};
  }[];
  position_review_open_count?: number;
};

// 服务操作可能只返回一次性确认/回复（challenge / reply），此时不得当作状态负载。
export type StockServiceResponse = StockServiceStatus & {challenge?: string; reply?: string};

export type StockModel = {
  id: string;
  provider: string;
  provider_name: string;
  model: string;
  kind: string;
  status: string;
};
