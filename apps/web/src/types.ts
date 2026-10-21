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
  id: string; source?: string; source_label?: string; kind: string; conversation_id: string | null; workspace_id: string;
  request_id: string; title: string; model: string; status: string; phase: string;
  created_at: string; updated_at: string; last_seq: number; error?: string | null;
  resumable: boolean; resume_blocker: string; checkpoint_available: boolean;
  business_status?: string; report_available?: boolean; actions?: string[];
  research_status?: string; publication_status?: string;
  artifacts?: {path:string;title:string;sha256:string}[];
};
export type RunList = {runs: RunSummary[]; shutdown_error?: string; page_connections?: number;
  providers?: Record<string, {online: boolean; title?: string; details?: string[]; message?: string}>;
  provider_errors?: Record<string, string>;
  stock_service?: {online: boolean; message?: string;
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

