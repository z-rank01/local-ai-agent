export type ReportSection = string;
export type ReportBlock = {id: string; kind: 'markdown' | 'disclosure'; content: string;
  title?: string; stage?: boolean; technical?: boolean; source_alias?: number};
export type ReportPage = {content: string; page: number; page_count: number; layout?: string;
  title?: string; revision?: string; section?: ReportSection; blocks?: ReportBlock[];
  section_counts?: Record<ReportSection, number>; source_pages?: Record<string, number>};

export function checkedReportPage(value: ReportPage, page: number, section: ReportSection): ReportPage {
  if (value.page !== page || !Number.isInteger(value.page_count) || value.page_count < page
      || typeof value.content !== 'string') throw new Error('报告页回执不一致，当前页面保留。');
  if (value.layout === 'research-reader-v2') {
    if (value.section !== section || !/^[0-9a-f]{64}$/.test(value.revision ?? '')
        || !Array.isArray(value.blocks) || !value.blocks.length
        || value.blocks.some(b => !b || typeof b.id !== 'string' || typeof b.content !== 'string'
          || !['markdown', 'disclosure'].includes(b.kind)
          || (b.kind === 'disclosure' && typeof b.title !== 'string'))) {
      throw new Error('报告展示结构无效，当前页面保留。');
    }
  }
  return value;
}
