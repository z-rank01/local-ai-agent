"""Explicit source-check transitions; never choose or rewrite tool arguments."""
from dataclasses import dataclass
from enum import Enum


def incomplete_delivery(index, question, records, *, authorised):
    """Retain receipt-backed navigation, not a rejected model conclusion.

    A source link only attests that saved original fragments were received;
    it does not attest to website coverage or the truth of a model inference.
    """
    message = '来源补查未返回工具调用，原文核对未完成；未将待核草稿作为研究结论交付。'
    if not authorised:
        return message
    from .stock_task_binding import reference_binding
    from .stock_source_coverage import canonical_span
    from .stock_receipts import successful
    binding = reference_binding(index, question)
    if binding['selection'] == 'all_known' or not binding['references']:
        return message
    selected = binding['references']
    urls = set()
    for record in records:
        if not successful(record) or record.get('source_reference') not in selected:
            continue
        for span in record.get('excerpts', []):
            original = canonical_span(span, record.get('url'))
            if original is not None:
                # Keep the existing public-source host gate and reject Markdown
                # delimiters/control characters rather than rewriting a URL.
                url = original['url']
                if not any(ord(char) < 32 or char in '<>' for char in url):
                    urls.add(url)
    retained = []
    if any(row.get('report_read') and row['source_reference'] in selected for row in index):
        retained.append('本任务已有读取成功的报告，可从会话中的原报告卡回看。')
    if urls:
        retained.append('已收到保存原文片段的来源：\n\n' + '\n'.join(
            f'- [来源{i}](<{url}>)' for i, url in enumerate(sorted(urls), 1)))
        retained.append('这些入口只对应已经收到的保存片段，不表示网站全文已读完，也不认证草稿中的推断。')
    if retained:
        message += '\n\n' + '\n\n'.join(retained)
    return message + '\n\n本轮未自动重发；已取得资料和未核验草稿仍保留在原会话。'


class Phase(str, Enum):
    EXPLANATION = 'explanation'
    FIND = 'find'
    READ = 'read'


@dataclass
class SourceReview:
    phase: Phase = Phase.EXPLANATION
    attempts: int = 0
    feedback: str = ''

    @property
    def parameter_step(self):
        return self.phase != Phase.EXPLANATION

    def begin(self, feedback):
        self.attempts += 1
        self.feedback = feedback
        self.phase = Phase.FIND

    def explain(self):
        self.phase = Phase.EXPLANATION

    def received(self, event):
        self.explain()
        result = event.get('result') or {}
        result = result.get('model_observation', result) if isinstance(result, dict) else {}
        if event.get('status') != 'ok' or not isinstance(result, dict):
            return
        if event.get('name') == 'stock_public_evidence_find':
            if result.get('items'):
                self.phase = Phase.READ
            elif result.get('kind') == 'public_evidence_find':
                self.phase = Phase.FIND
                self.feedback = ('本次查询未命中。原文是否存在尚不能确定；'
                    '下一步选择更短词语，或省略search列出本任务来源目录。'
                    '不要重复相同查询，不要生成事实结论。')
        elif event.get('name') == 'stock_public_evidence_read' and result.get('next_cursor'):
            self.phase = Phase.READ
