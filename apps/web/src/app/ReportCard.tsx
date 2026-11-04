import {useContext, useEffect, useState} from 'react';
import type {TranscriptBlock} from '../types';
import {getArtifactInfo, type ArtifactInfo} from '../extensions/artifacts';
import {buildGenericArtifactInfo, type ReadingReport} from '../lib/report';
import {BriefReaderContext} from '../lib/briefReader';
import {workspaceDocumentKey, workspaceDocumentKind} from '../lib/workspaceDocument';
import {ArtifactCard} from '../ui/ArtifactCard';

export type ReportCardProps = {
  info: ArtifactInfo;
  onRead: () => void;
};

export function ReportCard({info, onRead}: ReportCardProps): JSX.Element {
  return <ArtifactCard title={info.title} badge="本地报告" meta={info.meta}
    actionLabel="阅读全文" onOpen={onRead} />;
}

export function ArtifactBlockCard({block, onReadReport, conversationId}: {
  block: TranscriptBlock;
  onReadReport: (report: ReadingReport) => void;
  conversationId?: string;
}): JSX.Element | null {
  const [info, setInfo] = useState<ArtifactInfo | null>(null);
  const [error, setError] = useState('');
  const readDocument = useContext(BriefReaderContext);
  useEffect(() => {
    let active = true;
    getArtifactInfo(block, conversationId).then(value => {
      if (active) setInfo(value);
    }).catch(() => {
      if (active) {
        setInfo(buildGenericArtifactInfo(block));
        setError('成果样式不可用，已保留文档阅读。');
      }
    });
    return () => {active = false;};
  }, [block, conversationId]);
  const result = block.toolResult as {path?: unknown; title?: unknown} | null | undefined;
  const documentPath = typeof result?.path === 'string' ? workspaceDocumentKey(result.path) : '';
  const documentKind = typeof result?.path === 'string' ? workspaceDocumentKind(result.path) : null;
  if (error && documentPath && documentKind && readDocument) {
    const filename = documentPath.split('/').pop() || '文档';
    return <>
      <p className="panel-hint" role="alert">{error}</p>
      <ArtifactCard title={typeof result?.title === 'string' ? result.title : filename}
        badge="工作区文件" meta={[filename]} actionLabel={documentKind === 'html' ? '阅读简报' : '阅读文档'}
        onOpen={() => readDocument(documentPath)} />
    </>;
  }
  if (!info) return <p className="panel-hint">正在读取成果信息…</p>;
  return <>
    {error ? <p className="panel-hint" role="alert">{error}</p> : null}
    <ReportCard info={info} onRead={() => onReadReport(info)} />
  </>;
}
