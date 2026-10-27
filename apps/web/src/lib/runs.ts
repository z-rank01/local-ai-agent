import type {RunSummary,UIStreamEvent} from '../types';

export const RUN_LIVE = new Set(['RUNNING','PAUSING','PAUSED','STOPPING']);
const LABELS: Record<string,string>={RUNNING:'运行中',PAUSING:'正在暂停',PAUSED:'已暂停',STOPPING:'正在停止',
  SUCCEEDED:'已完成',FAILED:'失败',STOPPED:'已停止',INTERRUPTED:'已中断',QUEUED:'等待领取'};
export const runLabel=(status: string) => LABELS[status] ?? status;
export function shouldObserveRun(run: RunSummary | undefined, synced: Record<string,number>): run is RunSummary {
  return Boolean(run && (RUN_LIVE.has(run.status) || synced[run.id] !== run.last_seq));
}
export function conversationRun(rows: RunSummary[], id: string | null): RunSummary | undefined {
  if(!id) return undefined;
  const matches=rows.filter(r=>r.conversation_id===id&&r.source!=='plugin');
  return matches.find(r=>RUN_LIVE.has(r.status))??matches[0];
}
export function acceptRunEvent(cursors: Record<string,number>, event: UIStreamEvent): boolean {
  if(!event.run_id||typeof event.seq!=='number') return true;
  if(event.seq<=(cursors[event.run_id]??0)) return false;
  cursors[event.run_id]=event.seq;
  return true;
}
