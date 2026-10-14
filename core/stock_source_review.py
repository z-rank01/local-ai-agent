"""Explicit source-check transitions; never choose or rewrite tool arguments."""
from dataclasses import dataclass
from enum import Enum


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
