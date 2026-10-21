"""Describe a returned call separately from the task it may have submitted."""

_TASK_LABELS = {
    'QUEUED': '任务已排队', 'PENDING': '任务待执行',
    'RUNNING': '任务执行中', 'SUCCEEDED': '任务已完成',
    'PARTIAL': '任务部分完成', 'FAILED': '任务失败',
    'CANCELLED': '任务已取消', 'STOPPED': '任务已停止',
    'INTERRUPTED': '任务已中断', 'UNKNOWN': '任务结果未知',
}


def call_summary(result, status):
    if status != 'ok':
        return '调用失败'
    if isinstance(result, dict):
        state = str(result.get('state') or result.get('status') or '').upper()
        if state in _TASK_LABELS:
            return _TASK_LABELS[state]
    return '调用已返回'
