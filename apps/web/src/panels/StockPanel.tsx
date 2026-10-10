import {useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {fetchStockModels, fetchStockService, stockServiceAction} from '../api';
import type {ProviderInfo, StockModel, StockServiceStatus} from '../types';
import {ModelPicker} from '../app/ModelPicker';
import {notify} from '../lib/notify';
import {Accordion} from '../ui/Accordion';
import {Collapse} from '../ui/Collapse';
import {Icon} from '../ui/Icon';

export type StockPanelProps = {onChanged?: () => void};

type Step = NonNullable<StockServiceStatus['recovery']>['steps'][number];
type PositionReview = NonNullable<StockServiceStatus['position_reviews']>[number];

const labels: Record<string, string> = {UNINITIALIZED: '未初始化', ACTIVE: '正常运行', AWAY_READONLY: '离开 · 只读', RECOVERING: '恢复核对中', NEEDS_REVIEW: '需要核查', PENDING: '等待执行', RUNNING: '执行中', COMPLETED: '已完成', BLOCKED: '有阻塞'};
const label = (value: string) => labels[value] ?? value;

const pendingText: Record<string, string> = {
  configure: '保存中…',
  recover: '正在补跑…',
  prepare_resume: '正在核对…',
  confirm_resume: '正在恢复…',
  worker_start: '正在开启…',
  worker_pause: '正在暂停…',
  operator: '提交中…',
};

const magicWords = ['帮助', '账户', '权益待办', '恢复运行'];

function ReviewItem({head, children}: {head: string; children: ReactNode}): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div className="review" data-open={open ? 'true' : 'false'}>
      <button
        type="button"
        className="review-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="rmain">{head}</span>
        <Icon name="i-chev-down" className="ico-sm" />
      </button>
      <Collapse open={open} className="review-body">
        <div className="review-content">{children}</div>
      </Collapse>
    </div>
  );
}

export function StockPanel({onChanged}: StockPanelProps): JSX.Element {
  const [status, setStatus] = useState<StockServiceStatus | null>(null);
  const [marketModels, setMarketModels] = useState<StockModel[]>([]);
  const [root, setRoot] = useState('');
  const [stateDir, setStateDir] = useState('simulation');
  const [port, setPort] = useState('8765');
  const [busy, setBusy] = useState(false);
  const [researchOnly, setResearchOnly] = useState<boolean | undefined>(undefined);
  const [modelsRefreshing, setModelsRefreshing] = useState(false);
  const [pendingAction, setPendingAction] = useState('');
  const [error, setError] = useState('');
  const [challenge, setChallenge] = useState('');
  const [query, setQuery] = useState('');
  const [reply, setReply] = useState('');
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  /* 变更进行中时跳过轮询；revision 单调递增，丢弃迟到的读回结果 */
  const mutating = useRef(false);
  const revision = useRef(0);
  /* 只由轮询写入的错误在连通恢复后自动消失；操作错误保留到下一次操作 */
  const pollError = useRef(false);

  /* 简报分析模型目录来自后端；读取失败时选择器自己显示空态，刷新按钮可重试 */
  const refreshMarketModels = async (force = false) => {
    setModelsRefreshing(true);
    try {
      setMarketModels(await fetchStockModels(force));
    } catch {
      setMarketModels([]);
    } finally {
      setModelsRefreshing(false);
    }
  };

  useEffect(() => {
    void refreshMarketModels();
  }, []);

  useEffect(() => {
    let active = true;
    let first = true;
    const refresh = async () => {
      if (mutating.current) {
        return;
      }
      const requestRevision = ++revision.current;
      try {
        const value = await fetchStockService();
        if (!active || requestRevision !== revision.current) {
          return;
        }
        setStatus(value);
        if (pollError.current) {
          pollError.current = false;
          setError('');
        }
        if (first && value.settings) {
          setRoot(value.settings.root);
          setStateDir(value.settings.state_dir);
          setPort(String(value.settings.port));
          first = false;
        }
      } catch (cause) {
        if (active && requestRevision === revision.current && !pollError.current) {
          pollError.current = true;
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    if (mutating.current) {
      return;
    }
    mutating.current = true;
    ++revision.current;
    setPendingAction(action);
    setBusy(true);
    setError('');
    try {
      const result = await stockServiceAction({action, ...extra});
      if (result.challenge) {
        setChallenge(result.challenge);
      } else if (result.reply) {
        setReply(result.reply);
      } else {
        setStatus(result);
        setChallenge('');
      }
      onChanged?.();
      if (action === 'operator') {
        setQuery('');
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      pollError.current = false;
      setError(message);
      notify(message, 'error');
    } finally {
      mutating.current = false;
      setBusy(false);
      setPendingAction('');
    }
  };

  const steps: Step[] = status?.recovery?.steps ?? [];
  const online = Boolean(status?.online);
  const completed = steps.filter((step) => step.status === 'COMPLETED').length;
  const problems = status?.recovery?.problems ?? [];
  const humanTasks = status?.recovery?.human_tasks ?? [];
  const worker = status?.worker;
  const workerState = worker?.draining ? '正在收尾' : worker?.enabled ? '已开启' : '已暂停';
  const reviewsAvailable = Array.isArray(status?.position_reviews);
  const positionReviews: PositionReview[] = status?.position_reviews ?? [];
  const openReviews =
    status?.position_review_open_count ?? positionReviews.filter((item) => item.status === 'OPEN').length;
  const settingsMeta = status?.settings?.root
    ? `${status.settings.state_dir} · ${status.settings.port}`
    : '首次使用';
  const marketModel = status?.market_model;
  const marketName = marketModels.find(
    (item) => item.provider === marketModel?.provider && item.model === marketModel?.model,
  );
  const marketModelLabel = marketModel
    ? `${marketName?.provider_name ?? marketModel.provider} / ${marketModel.model}`
    : '尚未配置';
  /* 简报模型目录只带选择器真正渲染的字段（供应商、型号、可用状态）；
     其余模型元信息按中性值补齐，不臆造上下文窗口与能力 */
  const marketProviders: ProviderInfo[] = Array.from(new Set(marketModels.map((item) => item.provider))).map(
    (providerId) => {
      const group = marketModels.filter((item) => item.provider === providerId);
      return {
        id: providerId,
        name: group[0].provider_name,
        kind: group[0].kind,
        enabled: true,
        base_url: null,
        models: group.map((item) => ({
          id: item.id,
          name: item.model,
          provider_id: item.provider,
          provider_name: item.provider_name,
          default: false,
          capabilities: [],
          context_window: null,
          thinking_supported: false,
          thinking_budget_supported: false,
          thinking_enabled: null,
          thinking_budget: null,
          status: item.status,
        })),
      };
    },
  );
  const attention = Boolean(challenge) || problems.length > 0;
  const buttonLabel = (action: string, idle: string) =>
    busy && pendingAction === action ? pendingText[action] ?? '处理中…' : idle;
  const toggleGroup = (key: string) =>
    setOpenGroups((current) => ({...current, [key]: !current[key]}));

  useEffect(() => {
    if (!attention) {
      return;
    }
    setOpenGroups((current) => (current.recover ? current : {...current, recover: true}));
  }, [attention]);

  return (
    <>
      {error ? (
        <div className="panel-ok" role="alert">
          <Icon name="i-alert" className="ico-sm" />
          <span>{error}</span>
        </div>
      ) : null}

      {challenge ? (
        <>
          <div className="panel-ok" role="alert">
            <Icon name="i-alert" className="ico-sm" />
            <span>恢复确认待处理：确认已核对恢复结果与待办？恢复后允许正常研究；新订单仍需逐笔确认。</span>
          </div>
          <div className="row-lead">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={() => void act('confirm_resume', {challenge})}
            >
              {buttonLabel('confirm_resume', '确认恢复')}
            </button>
            <button type="button" className="btn btn-quiet btn-sm" disabled={busy} onClick={() => setChallenge('')}>
              取消
            </button>
          </div>
        </>
      ) : null}

      {!status ? <p className="panel-hint">正在读取股票后台状态…</p> : null}

      {online && status?.runtime ? (
        <div className="stock-summary">
          <div className="stock-line">
            <span>任务执行</span>
            <b>{workerState}</b>
          </div>
          <div className="stock-line">
            <span>业务模式</span>
            <b>{label(status.runtime.mode)}</b>
          </div>
          <div className="stock-line">
            <span>已核对至</span>
            <b>{status.runtime.last_completed_day ?? '尚无记录'}</b>
          </div>
        </div>
      ) : null}

      {status && !online ? (
        <div className="stock-summary">
          <div className="stock-line">
            <span>运行状态</span>
            <b>已关闭</b>
          </div>
        </div>
      ) : null}

      {status && !online ? <p className="panel-hint">关闭状态下不取出新任务、不生成简报。</p> : null}
      {status && !online && status.message ? <p className="panel-hint">{status.message}</p> : null}
      {status && !online && !status.settings?.root ? (
        <p className="panel-hint">首次使用请在下方“高级管理 → 连接配置”保存股票仓库路径。</p>
      ) : null}

      {online ? (
        <Accordion
          open={Boolean(openGroups.recover)}
          onToggle={() => toggleGroup('recover')}
          head="恢复与待办"
          meta={`${completed}/${steps.length} 已核对 · ${label(status?.recovery?.batch?.status ?? '无恢复批次')}`}
        >
          <p className="panel-hint">历史数据标为事后取得，离线期间未实时监控；恢复不自动重发模型请求。</p>
          {problems.map((problem) => (
            <div key={problem} className="panel-ok" role="status">
              <Icon name="i-alert" className="ico-sm" />
              <span>{problem}</span>
            </div>
          ))}
          {humanTasks.map((task) => (
            <div key={task.id} className="panel-ok" role="status">
              <Icon name="i-alert" className="ico-sm" />
              <span>{task.body?.message ?? '待人工处理：请到“人工操作”中查看。'}</span>
            </div>
          ))}
          <div className="row-lead">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={busy || Boolean(worker?.busy)}
              onClick={() => void act('recover')}
            >
              {buttonLabel('recover', '补跑 / 重试恢复')}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || Boolean(worker?.busy) || problems.length > 0}
              onClick={() => void act('prepare_resume')}
            >
              {buttonLabel('prepare_resume', '核对并恢复运行')}
            </button>
          </div>
        </Accordion>
      ) : null}

      {online ? (
        <Accordion
          open={Boolean(openGroups.reviews)}
          onToggle={() => toggleGroup('reviews')}
          head="持仓监督"
          meta={reviewsAvailable ? `${openReviews} 项待核查 · 最近 ${positionReviews.length} 项` : '后台状态未同步'}
        >
          {!reviewsAvailable ? (
            <p className="panel-hint">当前股票后台尚未提供持仓监督状态；待后台更新并完成任务收尾后再查看。</p>
          ) : null}
          {reviewsAvailable && positionReviews.length === 0 ? (
            <p className="panel-hint">暂无持仓监督记录；无持仓时不会触发监督流程。</p>
          ) : null}
          {positionReviews.map((item) => (
            <ReviewItem
              key={item.id}
              head={`${item.symbol || '历史记录'} · ${item.day} · ${item.status === 'OPEN' ? '待核查' : '已有监督结论'}`}
            >
              {item.review?.verdict || item.review?.status ? (
                <p>
                  <b>结论：</b>
                  {item.review?.verdict ?? item.review?.status}
                </p>
              ) : null}
              {item.reasons?.length ? (
                <p>
                  <b>触发：</b>
                  {item.reasons.join('；')}
                </p>
              ) : null}
              {item.review?.report ? <pre className="mono-block">{item.review.report}</pre> : null}
              {item.review?.missing?.length ? (
                <p>
                  <b>缺失：</b>
                  {item.review?.missing.join('；')}
                </p>
              ) : null}
              {item.review?.decision_id ? (
                <p className="panel-hint">
                  订单草案须人工核对。在下方“人工操作”输入 审查 {item.review.decision_id}，按返回提示继续。
                </p>
              ) : null}
            </ReviewItem>
          ))}
        </Accordion>
      ) : null}

      {online ? (
        <Accordion
          open={Boolean(openGroups.model)}
          onToggle={() => toggleGroup('model')}
          head="简报分析模型"
          meta={status?.research_model?.enabled ? 'Qwen / qwen3.8-flash · 直连' : marketModelLabel}
        >
          <div className="field">
            <span className="field-label">{status?.research_model?.enabled ? '直连关闭后使用的简报模型' : '每日简报分析模型'}</span>
            <ModelPicker
              providers={marketProviders}
              value={marketModel ? `${marketModel.provider}:${marketModel.model}` : ''}
              onChange={(modelId) => {
                const choice = marketModels.find((item) => item.id === modelId);
                if (choice) {
                  void act('market_model_configure', {
                    model: {provider: choice.provider, model: choice.model},
                  });
                }
              }}
              onRefresh={() => void refreshMarketModels(true)}
              refreshing={modelsRefreshing}
              disabled={busy || Boolean(status?.research_model?.enabled)}
            />
          </div>
          <p className="panel-hint">直连开启时，新简报与只读研究固定使用 qwen3.8-flash。对话模型独立，已入队任务保持原配置。</p>
          <label className="field">
            <span className="field-label">只读研究使用 Qwen 云端直连（验收中）</span>
            <input type="checkbox" checked={Boolean(status?.research_model?.enabled)} disabled={busy}
              onChange={(event) => void act('research_model_configure', {
                research: {enabled: event.target.checked, duties: ['C01', 'C02', 'C03', 'C04']},
              })} />
          </label>
          <p className="panel-hint">仅影响新研究及新简报。旧任务保持原模型；审查通路保持原设置。当前仍需完成质量验收。</p>
        </Accordion>
      ) : null}

      <Accordion
        open={Boolean(openGroups.advanced)}
        onToggle={() => toggleGroup('advanced')}
        head="高级管理"
        meta={settingsMeta}
      >
        <Accordion
          open={Boolean(openGroups.conn)}
          onToggle={() => toggleGroup('conn')}
          head="连接配置"
        >
          <label className="field">
            <span className="field-label">股票仓库路径</span>
            <input
              className="input input-sm"
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              placeholder="D:\Stock_Agent_Workspace"
            />
          </label>
          <label className="field">
            <span className="field-label">状态目录</span>
            <input
              className="input input-sm"
              value={stateDir}
              onChange={(event) => setStateDir(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field-label">端口</span>
            <input
              className="input input-sm"
              type="number"
              value={port}
              onChange={(event) => setPort(event.target.value)}
            />
          </label>
          <p className="panel-hint">日常目录为 simulation；验收请使用独立目录，避免切错账户。修改前请先关闭股票技能。</p>
          <div className="row-lead">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || online}
              onClick={() => void act('configure', {root, state_dir: stateDir, port: Number(port)})}
            >
              {buttonLabel('configure', '保存配置')}
            </button>
          </div>
        </Accordion>

        {online ? (
          <Accordion
            open={Boolean(openGroups.worker)}
            onToggle={() => toggleGroup('worker')}
            head="任务执行"
            meta={workerState}
          >
            <p className="panel-hint">暂停后后台任务不再取出新任务，进行中的研究会走到安全边界收尾。</p>
            <label className="field">
              <span className="field-label">仅执行主动提交的只读研究，暂停日常调度</span>
              <input type="checkbox" checked={researchOnly ?? Boolean(worker?.research_only)}
                disabled={busy || Boolean(worker?.enabled) || Boolean(worker?.busy)}
                onChange={(event) => setResearchOnly(event.target.checked)} />
            </label>
            <div className="row-lead">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy || Boolean(worker?.enabled)}
                onClick={() => void act('worker_start', {research_only: researchOnly ?? Boolean(worker?.research_only)})}
              >
                {buttonLabel('worker_start', '开启任务执行')}
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy || !worker?.enabled}
                onClick={() => void act('worker_pause')}
              >
                {buttonLabel('worker_pause', '暂停任务执行')}
              </button>
            </div>
          </Accordion>
        ) : null}

        {online ? (
          <Accordion
            open={Boolean(openGroups.daily)}
            onToggle={() => toggleGroup('daily')}
            head="逐日恢复记录"
            meta={steps.length ? `${steps.length} 天` : '暂无记录'}
          >
            {steps.length ? (
              <div className="review">
                <div className="log-lines">
                  {steps.map((step) => (
                    <div key={step.id}>
                      {`${step.body?.day ?? '日期未知'} · ${label(step.status)}${step.body?.error ? `：${step.body.error}` : ''}`}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="panel-hint">暂无逐日恢复记录。</p>
            )}
          </Accordion>
        ) : null}

        {online ? (
          <Accordion
            open={Boolean(openGroups.human)}
            onToggle={() => toggleGroup('human')}
            head="人工操作"
            meta="直接提交，不经过模型"
          >
            <p className="panel-hint">
              可输入“帮助”“账户”“权益待办”“恢复运行”“初始化 100000”。需要确认的操作会返回一次性确认指令；复制到此处提交。
            </p>
            <div className="magic-row">
              {magicWords.map((word) => (
                <button
                  key={word}
                  type="button"
                  className="magic"
                  onClick={() => setQuery((current) => (current.trim() ? `${current.trimEnd()} ${word}` : word))}
                >
                  {word}
                </button>
              ))}
            </div>
            <textarea
              className="textarea"
              rows={2}
              aria-label="人工操作内容"
              placeholder="输入操作内容"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="row-lead">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={busy || !query.trim()}
                onClick={() => void act('operator', {query, request_id: crypto.randomUUID()})}
              >
                {buttonLabel('operator', '提交')}
              </button>
            </div>
            {reply ? <pre className="mono-block">{reply}</pre> : null}
          </Accordion>
        ) : null}
      </Accordion>
    </>
  );
}
