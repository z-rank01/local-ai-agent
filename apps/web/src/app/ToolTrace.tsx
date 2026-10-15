import {Fragment, type ReactNode} from 'react';
import {
  formatJsonText,
  formatTime,
  formatToolElapsed,
  formatToolParamValue,
  highlightText,
  roleLabel,
  structuredToolPayload,
} from '../lib/format';
import {useToolNames} from '../lib/workspace';
import {isLocalReportBlock} from '../lib/report';
import type {AssistantTimelineEntry} from '../lib/transcript';
import type {TranscriptBlock} from '../types';
import {Collapse} from '../ui/Collapse';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';

export type ToolTraceProps = {
  steps: AssistantTimelineEntry[];
  blocks: TranscriptBlock[];
  open: boolean;
  onToggle: () => void;
  onToggleStep: (stepId: string) => void;
  onOpenConversation?: (conversationId: string) => void | Promise<void>;
};

const STEP_KIND: Record<AssistantTimelineEntry['kind'], {kind: string; label: string}> = {
  reasoning: {kind: 'think', label: '思考'},
  tool: {kind: 'tool', label: '工具'},
  assistant: {kind: 'answer', label: '回答'},
};

const TOOL_TEXT_LIMIT = 102400;
const TOOL_TEXT_TRUNCATION_NOTICE = '（内容过长，仅显示最近 100KB）';

type TraceTone = 'ok' | 'error' | 'running';

type ObservationChip = {
  label: string;
  value: string;
  warn?: boolean;
};

type ConversationHit = {
  conversation_id: string;
  title: string;
  model?: string;
  updated_at?: string;
  matched_role?: string | null;
  snippet: string;
};

type ConversationSearchPayload = {
  query?: string;
  results?: ConversationHit[];
};

type ConversationReadMessage = {
  id: string;
  role: string;
  created_at?: string;
  content?: string;
};

type ConversationReadPayload = {
  conversation?: {
    id: string;
    title: string;
    model?: string;
    updated_at?: string;
  };
  message_count?: number;
  messages?: ConversationReadMessage[];
};

export function statusTone(status?: TranscriptBlock['status']): TraceTone {
  if (status === 'error') {
    return 'error';
  }
  if (status === 'running') {
    return 'running';
  }
  return 'ok';
}

function stepIcon(tone: TraceTone): string {
  if (tone === 'ok') {
    return 'i-check';
  }
  return tone === 'error' ? 'i-x' : 'i-chev-right';
}

function observationChips(block: TranscriptBlock): ObservationChip[] {
  if (!block.label.startsWith('stock_')) {
    return [];
  }
  const toolResult = block.toolResult && typeof block.toolResult === 'object' && !Array.isArray(block.toolResult)
    ? block.toolResult as Record<string, unknown>
    : null;
  const observation = toolResult?.model_observation && typeof toolResult.model_observation === 'object' && !Array.isArray(toolResult.model_observation)
    ? toolResult.model_observation as Record<string, unknown>
    : null;
  if (!observation) {
    return [];
  }
  if (block.label === 'stock_account_view') {
    const account = observation.account && typeof observation.account === 'object' && !Array.isArray(observation.account)
      ? observation.account as Record<string, unknown>
      : null;
    if (!account) {
      return [];
    }
    const chips: ObservationChip[] = [];
    if (typeof account.status === 'string' && account.status) {
      chips.push({label: '状态', value: account.status});
    }
    if (typeof account.cash_yuan === 'number') {
      chips.push({label: '现金', value: `¥${account.cash_yuan.toLocaleString('zh-CN')}`});
    }
    if (typeof account.position_count === 'number') {
      chips.push({label: '持仓', value: `${account.position_count} 只`});
    }
    if (account.value_basis === 'LEDGER_COST_NOT_MARKET_VALUE') {
      chips.push({label: '口径', value: '账面成本非市值', warn: true});
    }
    return chips;
  }
  const chips: ObservationChip[] = [];
  for (const [key, label] of [['task_status', '任务'], ['business_status', '裁决'], ['data_quality', '数据']] as const) {
    const value = observation[key];
    if (typeof value === 'string' && value) {
      chips.push({label, value});
    }
  }
  if (typeof observation.available_at === 'string' && observation.available_at) {
    chips.push({label: '证据时间', value: observation.available_at});
  }
  return chips;
}

function resultText(block: TranscriptBlock): string {
  const displayResult = block.toolResult && typeof block.toolResult === 'object' && !Array.isArray(block.toolResult)
    ? Object.fromEntries(Object.entries(block.toolResult as Record<string, unknown>)
        .filter(([key]) => key !== 'local_result' && key !== 'broker_sequence'))
    : block.toolResult;
  const structuredText = displayResult !== undefined && displayResult !== null ? formatToolParamValue(displayResult) : '';
  const text = structuredText || formatJsonText(block.text);
  return text.length > TOOL_TEXT_LIMIT
    ? `${TOOL_TEXT_TRUNCATION_NOTICE}\n${text.slice(-TOOL_TEXT_LIMIT)}`
    : text;
}

function DetailSection({title, children}: {title: string; children: ReactNode}): JSX.Element {
  return (
    <div className="detail-sec">
      <div className="detail-sec-head">{title}</div>
      {children}
    </div>
  );
}

function OpenableHit({
  onOpen,
  children,
}: {
  onOpen?: () => void;
  children: ReactNode;
}): JSX.Element {
  if (!onOpen) {
    return <div className="hit">{children}</div>;
  }
  return <button type="button" className="hit" onClick={onOpen}>{children}</button>;
}

function StepDetail({
  block,
  onOpenConversation,
}: {
  block: TranscriptBlock;
  onOpenConversation?: (conversationId: string) => void | Promise<void>;
}): JSX.Element {
  const openConversation = (conversationId: string): void => {
    if (onOpenConversation && conversationId) {
      void onOpenConversation(conversationId);
    }
  };
  if (isLocalReportBlock(block)) {
    return (
      <div className="step-detail-card">
        <DetailSection title="内部研究材料">
          <div className="detail-pre">材料保留在原研究记录中，供后续分析与核对；此处不作为用户简报展示。</div>
        </DetailSection>
      </div>
    );
  }
  const observations = observationChips(block);
  const searchPayload = block.label === 'conversation_search' ? structuredToolPayload<ConversationSearchPayload>(block) : null;
  const readPayload = block.label === 'conversation_read' ? structuredToolPayload<ConversationReadPayload>(block) : null;
  const searchQuery = typeof block.params?.query === 'string'
    ? block.params.query
    : typeof searchPayload?.query === 'string'
      ? searchPayload.query
      : '';
  const hits = searchPayload?.results ?? [];
  const source = readPayload?.conversation ?? null;
  const messages = readPayload?.messages ?? [];
  const hasParams = Boolean(block.params && Object.keys(block.params).length);
  const running = block.status === 'running';
  return (
    <div className="step-detail-card">
      {observations.length ? (
        <DetailSection title="观察">
          <div className="obs">
            {observations.map((chip) => (
              <span key={chip.label} className="obs-chip" data-tone={chip.warn ? 'warn' : undefined}>
                {chip.label} <b>{chip.value}</b>
              </span>
            ))}
          </div>
        </DetailSection>
      ) : null}
      {block.kind === 'reasoning' ? (
        <DetailSection title="推理">
          {block.text
            ? <Markdown content={block.text} />
            : <div className="detail-pre">正在思考…</div>}
        </DetailSection>
      ) : null}
      {hasParams ? (
        <DetailSection title="参数">
          <dl className="detail-kv">
            {Object.entries(block.params ?? {}).map(([key, value]) => (
              <Fragment key={key}>
                <dt>{key}</dt>
                <dd>{formatToolParamValue(value)}</dd>
              </Fragment>
            ))}
          </dl>
        </DetailSection>
      ) : null}
      {hits.length ? (
        <DetailSection title="命中">
          <div className="hits">
            {hits.map((hit) => (
              <OpenableHit
                key={hit.conversation_id}
                onOpen={onOpenConversation ? () => openConversation(hit.conversation_id) : undefined}
              >
                <span className="hit-title">{highlightText(hit.title, searchQuery)}</span>
                {hit.snippet ? <span className="hit-snip">{highlightText(hit.snippet, searchQuery)}</span> : null}
                <span className="hit-foot">
                  <span className="meta">
                    {roleLabel(hit.matched_role)}
                    {hit.updated_at ? ` · ${formatTime(hit.updated_at)}` : ''}
                    {hit.model ? ` · ${hit.model}` : ''}
                  </span>
                  {onOpenConversation ? <span className="link">打开会话</span> : null}
                </span>
              </OpenableHit>
            ))}
          </div>
        </DetailSection>
      ) : null}
      {source ? (
        <DetailSection title="来源会话">
          <div className="hits">
            <OpenableHit onOpen={onOpenConversation && source.id ? () => openConversation(source.id) : undefined}>
              <span className="hit-title">{source.title}</span>
              <span className="hit-foot">
                <span className="meta">
                  {`来源会话${source.updated_at ? ` · ${formatTime(source.updated_at)}` : ''}${source.model ? ` · ${source.model}` : ''}${typeof readPayload?.message_count === 'number' ? ` · ${readPayload.message_count} 条消息` : ''}`}
                </span>
                {onOpenConversation && source.id ? <span className="link">打开会话</span> : null}
              </span>
            </OpenableHit>
            {messages.map((message) => (
              <div key={message.id} className="hit">
                <span className="hit-title">{roleLabel(message.role).replace('命中', '')}</span>
                <span className="hit-snip">{message.content?.trim() || '(空)'}</span>
                {message.created_at ? (
                  <span className="hit-foot"><span className="meta">{formatTime(message.created_at)}</span></span>
                ) : null}
              </div>
            ))}
          </div>
        </DetailSection>
      ) : null}
      {block.kind === 'tool' && !hits.length && !source ? (
        <DetailSection title={running ? '执行中' : '结果'}>
          {block.text || block.toolResult !== undefined ? (
            <div className="detail-pre">{resultText(block)}</div>
          ) : (
            <div className="detail-pre">
              <span className="dots" aria-hidden="true"><i /> <i /> <i /></span>{' 正在调用工具…'}
            </div>
          )}
        </DetailSection>
      ) : null}
    </div>
  );
}

export function ToolTrace({steps, blocks, open, onToggle, onToggleStep, onOpenConversation}: ToolTraceProps): JSX.Element {
  const tone: TraceTone = steps.some((step) => step.status === 'error')
    ? 'error'
    : steps.some((step) => step.status === 'running')
      ? 'running'
      : 'ok';
  const stateLabel = tone === 'error' ? '有失败步骤' : tone === 'running' ? '进行中' : '完成';
  const totalElapsed = steps.reduce((sum, step) => sum + (typeof step.elapsed === 'number' ? step.elapsed : 0), 0);
  const names = useToolNames();
  steps = steps.map(step => {
    if (step.kind !== 'tool') return step;
    const block = blocks.find(block => block.id === step.id);
    const raw = block?.label ?? step.title;
    const name = names[raw] ?? '工具操作';
    return {...step, title: step.title.split(raw).join(name), subtitle: step.subtitle.split(raw).join(name)};
  });
  return (
    <section className="trace" data-open={open ? 'true' : 'false'} aria-label="执行轨迹">
      <button className="trace-head" type="button" aria-expanded={open} onClick={onToggle}>
        <span className="trace-title">执行轨迹</span>
        <span className="trace-stat">
          {steps.length} 步{totalElapsed > 0 ? ` · ${formatToolElapsed(totalElapsed)}` : ''}
        </span>
        <span className="trace-state" data-tone={tone}>{stateLabel}</span>
        <span className="trace-toggle"><Icon name="i-chev-down" className="ico-sm" /></span>
      </button>
      <Collapse open={open} className="steps-body">
        <ol className="steps">
          {steps.map((step) => {
            const block = blocks.find((item) => item.id === step.id) ?? null;
            const stepKind = STEP_KIND[step.kind];
            const stepTone = statusTone(step.status);
            const hasDetail = step.collapsible && block !== null;
            const detailOpen = hasDetail && !step.collapsed;
            const name = step.kind === 'tool' && block ? names[block.label] ?? '工具操作' : step.title;
            const head = (
              <>
                <span className="step-kind" data-kind={stepKind.kind}>{stepKind.label}</span>
                <span className="step-name">{name}</span>
                {step.badges.map((badge) => (
                  <span key={badge} className="step-tag" data-tone={badge.includes('失败') ? 'error' : undefined}>{badge}</span>
                ))}
                {step.elapsed ? <span className="step-dur">{formatToolElapsed(step.elapsed)}</span> : null}
                {hasDetail ? <span className="step-toggle">{detailOpen ? '收起' : '展开'}</span> : null}
              </>
            );
            return (
              <li
                key={step.id}
                className="step"
                data-state={stepTone}
                data-kind={stepKind.kind}
                data-open={detailOpen ? 'true' : 'false'}
              >
                <span className="step-node"><Icon name={stepIcon(stepTone)} className="ico-sm" /></span>
                {hasDetail ? (
                  <button type="button" className="step-top" aria-expanded={detailOpen} onClick={() => onToggleStep(step.id)}>
                    {head}
                  </button>
                ) : (
                  <div className="step-top">{head}</div>
                )}
                {step.subtitle ? <p className="step-sub">{step.subtitle}</p> : null}
                {hasDetail && block ? (
                  <Collapse open={detailOpen} className="step-detail">
                    <StepDetail block={block} onOpenConversation={onOpenConversation} />
                  </Collapse>
                ) : null}
              </li>
            );
          })}
        </ol>
      </Collapse>
    </section>
  );
}
