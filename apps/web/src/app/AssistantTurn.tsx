import {useState} from 'react';
import {formatTime} from '../lib/format';
import {buildReportCardInfo, isLocalReportBlock, type ReadingReport} from '../lib/report';
import {
  assistantGroupStatus,
  buildAssistantTimeline,
  type AssistantTimelineEntry,
  type AssistantTranscriptGroup,
} from '../lib/transcript';
import {statusLabel} from '../statusLabels';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';
import {ReportCard} from './ReportCard';
import {statusTone, ToolTrace} from './ToolTrace';

export type AssistantTurnProps = {
  item: AssistantTranscriptGroup;
  busy: boolean;
  onToggleBlock: (blockId: string) => void;
  onCopy: (text: string) => void;
  onDelete: (messageId: string) => void;
  onRegenerate: (userMessageId?: string) => void;
  onSwitchVersion: (messageId: string, direction: -1 | 1) => void;
  onReadReport: (report: ReadingReport) => void;
  onOpenConversation?: (conversationId: string) => void | Promise<void>;
};

function withoutVersionBadge(step: AssistantTimelineEntry): AssistantTimelineEntry {
  return {...step, badges: step.badges.filter((badge) => !badge.startsWith('版本'))};
}

function LoadingLine({label}: {label: string}): JSX.Element {
  return (
    <p className="loading">
      <span className="dots" aria-hidden="true"><i /> <i /> <i /></span>{label}
    </p>
  );
}

export function AssistantTurn({
  item,
  busy,
  onToggleBlock,
  onCopy,
  onDelete,
  onRegenerate,
  onSwitchVersion,
  onReadReport,
  onOpenConversation,
}: AssistantTurnProps): JSX.Element {
  const [traceOpen, setTraceOpen] = useState(true);
  const ordered = item.blocks;
  const status = assistantGroupStatus(ordered);
  const isRunning = status === 'running';
  const createdAt = ordered.find((block) => block.createdAt)?.createdAt;
  const hasAnswer = ordered.some((block) => block.kind === 'assistant');
  const answerBlock = [...ordered].reverse().find((block) => block.kind === 'assistant') ?? null;
  const answerText = ordered.filter((block) => block.kind === 'assistant').map((block) => block.text).join('\n\n');
  const answerMessageId = answerBlock?.messageId ?? null;
  const versionNumber = answerBlock?.versionNumber ?? 1;
  const versionCount = answerBlock?.versionCount ?? 1;
  const canSwitchVersion = Boolean(answerMessageId) && versionCount > 1;
  const modelLabel = answerBlock && answerBlock.label && answerBlock.label !== '助手' ? answerBlock.label : '';
  const timeline = buildAssistantTimeline(ordered, status, versionNumber, versionCount);
  const traceSteps = timeline.map(withoutVersionBadge);
  const showTrace = traceSteps.some((step) => step.kind !== 'assistant') || traceSteps.length > 1;
  return (
    <article className="msg msg-ai" data-turn={item.id}>
      <header className="msg-head">
        <span className="msg-who">助手</span>
        {status ? <span className="msg-status" data-tone={statusTone(status)}>{statusLabel(status)}</span> : null}
        {modelLabel ? <span className="meta">{modelLabel}</span> : null}
        {createdAt ? <span className="meta">· {formatTime(createdAt)}</span> : null}
      </header>
      {showTrace ? (
        <ToolTrace
          steps={traceSteps}
          blocks={ordered}
          open={traceOpen}
          onToggle={() => setTraceOpen((current) => !current)}
          onToggleStep={onToggleBlock}
          onOpenConversation={onOpenConversation}
        />
      ) : null}
      {ordered.map((block) => {
        if (isLocalReportBlock(block)) {
          const info = buildReportCardInfo(block);
          return (
            <ReportCard
              key={block.id}
              info={info}
              onRead={() => onReadReport(info)}
            />
          );
        }
        if (showTrace && block.kind !== 'assistant') {
          return null;
        }
        if (block.kind === 'assistant') {
          if (!block.text && block.status !== 'running') {
            return null;
          }
          return block.text
            ? <Markdown key={block.id} content={block.text} />
            : <LoadingLine key={block.id} label="正在组织回答…" />;
        }
        return block.text ? <Markdown key={block.id} content={block.text} /> : null;
      })}
      {!hasAnswer && isRunning ? <LoadingLine label="正在等待模型输出…" /> : null}
      <div className="msg-acts">
        {canSwitchVersion && answerMessageId ? (
          <span className="version-pill" aria-label="回答版本">
            <button
              type="button"
              disabled={busy || isRunning || versionNumber <= 1}
              onClick={() => onSwitchVersion(answerMessageId, -1)}
              title="上一版"
              aria-label="上一版"
            ><Icon name="i-chev-left" className="ico-sm" /></button>
            <span className="num">{versionNumber} / {versionCount}</span>
            <button
              type="button"
              disabled={busy || isRunning || versionNumber >= versionCount}
              onClick={() => onSwitchVersion(answerMessageId, 1)}
              title="下一版"
              aria-label="下一版"
            ><Icon name="i-chev-right" className="ico-sm" /></button>
          </span>
        ) : null}
        <button
          type="button"
          className="msg-act"
          disabled={!answerText}
          onClick={() => onCopy(answerText)}
          title="复制回答"
        ><Icon name="i-copy" className="ico-sm" />复制</button>
        <button
          type="button"
          className="msg-act"
          disabled={busy}
          onClick={() => onRegenerate(answerBlock?.responseToMessageId)}
          title="重新生成"
        ><Icon name="i-refresh" className="ico-sm" />重新生成</button>
        {answerMessageId ? (
          <button
            type="button"
            className="msg-act"
            disabled={busy || isRunning}
            onClick={() => onDelete(answerMessageId)}
            title="删除此回答"
          ><Icon name="i-trash" className="ico-sm" />删除</button>
        ) : null}
      </div>
    </article>
  );
}
