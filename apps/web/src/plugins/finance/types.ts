export type StockServiceStatus = {
  online: boolean;
  message?: string;
  market_model?: {provider: string; model: string};
  research_model?: {enabled: boolean; provider: string; model: string; duties: string[]};
  settings: {root: string; state_dir: string; port: number; enabled: boolean; worker: boolean};
  worker?: {enabled: boolean; draining: boolean; busy: boolean; research_only?: boolean; recovery_only?: boolean; conversation_id?: string; model?: string};
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
