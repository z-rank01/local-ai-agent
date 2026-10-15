import type {buildReportCardInfo} from '../lib/report';
import {ArtifactCard} from '../ui/ArtifactCard';

export type ReportCardProps = {
  info: ReturnType<typeof buildReportCardInfo>;
  onRead: () => void;
};

export function ReportCard({info, onRead}: ReportCardProps): JSX.Element {
  return <ArtifactCard title={info.title} badge="本地报告" meta={info.meta}
    actionLabel="阅读全文" onOpen={onRead} />;
}
