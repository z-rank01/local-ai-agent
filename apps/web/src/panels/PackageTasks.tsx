import {Fragment, useEffect, useId, useState} from 'react';
import {useWorkspaceId} from '../lib/workspace';
import {cancelPackageJob, fetchPackageJobs} from '../api';
import type {PackageJob} from '../types';
import {Collapse} from '../ui/Collapse';
import {Icon} from '../ui/Icon';

const POLL_MS = 3000;

const JOB_LABELS: Record<string, string> = {
  queued: '等待安装',
  running: '正在安装',
  succeeded: '安装完成',
  failed: '安装失败',
  cancelled: '已停止',
  interrupted: '已中断',
};

function jobLabel(status: string): string {
  return JOB_LABELS[status] ?? '状态未知';
}

/* 收起态的一行摘要：有任务在跑时取第一条活动任务并附上后端给出的已等待秒数，
   全部结束时只报列表里第一条记录的状态；没有真实进度就不报百分比。 */
function jobSummary(jobs: PackageJob[]): string {
  const active = jobs.filter((job) => !job.completed);
  const head = active[0] ?? jobs[0];
  if (!head) {
    return '';
  }
  const names = head.packages.join('、');
  const label = names ? `${jobLabel(head.status)} ${names}` : jobLabel(head.status);
  if (!active.length) {
    return label;
  }
  const waited = `已等待 ${Math.floor(head.elapsed)} 秒`;
  return active.length === 1 ? `${label} · ${waited}` : `${label} 等 ${active.length} 个任务 · ${waited}`;
}

/* 会话抽屉底部的打包任务卡：3 秒自调度轮询 /api/package-jobs，
   没有任务且没有错误时整卡不占位。进度条只在有真实百分比时才该出现，
   这里只有后端给出的状态与已等待秒数，所以用跳动状态点表示进行中。 */
export function PackageTasks(): JSX.Element | null {
  const workspaceId = useWorkspaceId();
  return <WorkspacePackageTasks key={workspaceId} workspaceId={workspaceId} />;
}

function WorkspacePackageTasks({workspaceId}: {workspaceId: string}): JSX.Element | null {
  const [jobs, setJobs] = useState<PackageJob[]>([]);
  const [error, setError] = useState('');
  const [stopping, setStopping] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const bodyId = useId();

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const data = await fetchPackageJobs(controller.signal, undefined, workspaceId);
        if (!disposed) {
          setJobs(data);
          setError('');
        }
      } catch {
        if (!disposed) {
          setError('暂时无法获取安装状态，请检查工具服务。');
        }
      } finally {
        if (!disposed) {
          timer = setTimeout(refresh, POLL_MS);
        }
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [workspaceId]);

  const cancel = async (jobId: string) => {
    setStopping(jobId);
    try {
      const job = await cancelPackageJob(jobId, undefined, workspaceId);
      setJobs((current) => current.map((item) => (item.job_id === jobId ? job : item)));
      setError('');
    } catch {
      setError('停止安装失败，请重试。');
    } finally {
      setStopping(null);
    }
  };

  if (!jobs.length && !error) {
    return null;
  }

  const active = jobs.filter((job) => !job.completed);

  return (
    <section className="job-card" data-open={open ? 'true' : 'false'} aria-label="后台安装任务">
      <button
        type="button"
        className="job-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((current) => !current)}
      >
        <Icon name="i-download" className="ico" />
        <span className="job-name">
          {active.length ? `后台安装中 · ${active.length} 个任务` : '安装任务记录'}
          {error ? ' · 状态暂不可用' : ''}
        </span>
        <Icon name="i-chev-down" className="ico-sm" />
      </button>
      <div className="job-status">
        {active.length > 0 ? (
          <>
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>{' '}
          </>
        ) : null}
        {jobSummary(jobs)}
      </div>
      <Collapse open={open} className="job-body">
        <div className="job-body-card" id={bodyId}>
          <p className="job-note">安装无固定总时限，可继续聊天。停止回答不会停止安装；需要取消时使用下方按钮。</p>
          {error ? (
            <p className="notice notice-err" role="status">
              {error}
            </p>
          ) : null}
          {jobs.map((job) => (
            <Fragment key={job.job_id}>
              <div className="job-row">
                <span>{job.packages.join('、')}</span>
                <span className="num">
                  {jobLabel(job.status)} · {Math.floor(job.elapsed)} 秒
                </span>
              </div>
              {job.skipped.length ? <p className="job-note">已满足，跳过：{job.skipped.join('、')}</p> : null}
              {job.error ? <p className="notice notice-err">{job.error}</p> : null}
              {job.completed ? null : (
                <div className="row-lead">
                  <button
                    type="button"
                    className="btn btn-quiet btn-sm"
                    disabled={stopping === job.job_id}
                    onClick={() => void cancel(job.job_id)}
                  >
                    停止安装
                  </button>
                </div>
              )}
              <pre className="job-log">{job.log || '等待安装进程输出…'}</pre>
            </Fragment>
          ))}
        </div>
      </Collapse>
    </section>
  );
}
