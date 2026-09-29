const STATUS_LABELS: Record<string, string> = {
  running: '进行中',
  ok: '完成',
  error: '失败',
  interrupted: '已中断',
  pending: '等待中',
  idle: '空闲',
};
export const statusLabel = (status: string): string => STATUS_LABELS[status] ?? status;
