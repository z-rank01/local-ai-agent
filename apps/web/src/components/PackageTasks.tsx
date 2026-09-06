import {useEffect, useState} from 'react';
import {DEFAULT_BASE_URL} from '../api';

type PackageJob = {job_id: string; status: string; packages: string[]; skipped: string[]; elapsed: number; log: string; error?: string; completed: boolean};
const labels: Record<string,string> = {queued:'等待安装',running:'正在安装',succeeded:'安装完成',failed:'安装失败',cancelled:'已停止',interrupted:'已中断'};

export function PackageTasks() {
  const [jobs,setJobs] = useState<PackageJob[]>([]);
  const [error,setError] = useState('');
  const [stopping,setStopping] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await fetch(`${DEFAULT_BASE_URL}/api/package-jobs`, {signal:controller.signal});
        if (!response.ok) throw new Error('暂时无法获取安装状态，请检查工具服务。');
        const data = await response.json() as PackageJob[];
        if (!disposed) {setJobs(data); setError('');}
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : '状态获取失败');
      } finally {if (!disposed) timer = setTimeout(refresh,3000);}
    };
    void refresh();
    return () => {disposed=true; clearTimeout(timer); controller.abort();};
  }, []);
  const cancel = async (jobId: string) => {
    setStopping(jobId);
    try {
      const response = await fetch(`${DEFAULT_BASE_URL}/api/package-jobs/${encodeURIComponent(jobId)}/cancel`, {method:'POST'});
      if (!response.ok) throw new Error('停止安装失败，请重试。');
      const job = await response.json() as PackageJob;
      setJobs(current => current.map(item => item.job_id === jobId ? job : item));
      setError('');
    } catch (cause) {setError(cause instanceof Error ? cause.message : '停止失败');}
    finally {setStopping(null);}
  };
  if (!jobs.length && !error) return null;
  const active = jobs.filter(job => !job.completed);
  return <section className="package-tasks" aria-label="后台安装任务">
    <details>
      <summary>{active.length ? `后台安装中 · ${active[0].packages.join('、')} · 已等待 ${Math.floor(active[0].elapsed)} 秒` : '安装任务记录'}{error ? ' · 状态暂不可用' : ''}</summary>
      <div className="package-task-list">
        <p>安装无固定总时限，可继续聊天。停止回答不会停止安装；需要取消时使用下方按钮。</p>
        {error ? <p role="status">{error}</p> : null}
        {jobs.map(job => <article key={job.job_id}>
          <strong>{job.packages.join('、')}</strong>
          <p>{labels[job.status] ?? job.status} · {Math.floor(job.elapsed)} 秒</p>
          {job.skipped?.length ? <p>已满足，跳过：{job.skipped.join('、')}</p> : null}
          {job.error ? <p>{job.error}</p> : null}
          {!job.completed ? <button type="button" className="ghost-button tiny" disabled={stopping === job.job_id} onClick={() => void cancel(job.job_id)}>停止安装</button> : null}
          <details><summary>安装日志</summary><pre>{job.log || '等待安装进程输出…'}</pre></details>
        </article>)}
      </div>
    </details>
  </section>;
}
