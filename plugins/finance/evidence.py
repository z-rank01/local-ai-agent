"""Turn-local receipt index, incremented only when protocol material changes."""
import json

from plugins.finance.receipts import paired_receipts
from plugins.finance.source_quotes import source_task_index
from plugins.finance.company_context import context_index
from plugins.finance.calculation_review import calculation_index


class EvidenceIndex:
    def __init__(self):
        self.keys = []
        self.calls = {}
        self.receipts = []
        self.sources = []
        self.comparisons = {}
        self.calculations = {}

    def update(self, messages):
        protocol = [m for m in messages if m.get('role') == 'tool'
                    or m.get('role') == 'assistant' and m.get('tool_calls')]
        keys = [(m.get('role'), m.get('tool_name'), m.get('tool_call_id'), m.get('content'),
                 json.dumps(m.get('tool_calls'), sort_keys=True, ensure_ascii=False)) for m in protocol]
        if keys == self.keys:
            return False
        offset = len(self.keys)
        if keys[:offset] != self.keys:
            # A lossless projection/compaction may replace the protocol view.
            # Rebuild provenance rather than reusing stale positional bindings.
            self.calls, self.receipts, offset = {}, [], 0
        self.receipts.extend(paired_receipts(protocol[offset:], _calls=self.calls, _offset=offset))
        self.keys = keys
        self.sources = source_task_index((), receipts=self.receipts)
        self.comparisons = context_index((), receipts=self.receipts)
        self.calculations = calculation_index((), receipts=self.receipts)
        return True
