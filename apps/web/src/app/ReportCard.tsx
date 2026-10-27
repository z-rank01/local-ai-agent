import {useEffect, useState} from 'react';
import type {TranscriptBlock} from '../types';
import {getArtifactInfo, type ArtifactInfo} from '../extensions/artifacts';
import {buildGenericArtifactInfo, type ReadingReport} from '../lib/report';
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
  useEffect(() => {
    let active = true;
    getArtifactInfo(block, conversationId).then(value => {
      if (active) setInfo(value);
    }).catch(() => {
      if (active) {
        setInfo(buildGenericArtifactInfo(block));
        setError('成果样式不可用，已保留正文阅读。');
      }
    });
    return () => {active = false;};
  }, [block, conversationId]);
  if (!info) return <p className="panel-hint">正在读取成果信息…</p>;
  return <>
    {error ? <p className="panel-hint" role="alert">{error}</p> : null}
    <ReportCard info={info} onRead={() => onReadReport(info)} />
  </>;
}
