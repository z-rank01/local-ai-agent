"""Locate model-view material using successful receipts, never user intent.

The singleton fallback only locates existing material. It neither rewrites
questions/tool arguments nor grants access to a report or a research task.
"""
import re

VERSION = 'single-report-receipt-binding-v1'


def explicit_references(question):
    return set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', question or ''))


def reference_binding(index, question, current=()):
    known = {row['source_reference'] for row in index}
    requested = explicit_references(question)
    if requested:
        return {'references': (requested | set(current)) & known,
                'selection': 'explicit_partial' if requested - known else 'explicit_known',
                'version': 'current-task-lookup-index-v1'}
    if current:
        return {'references': set(current) & known, 'selection': 'current_receipt', 'version': None}
    # Multiple task pointers remain ambiguous, even if only one has a report.
    if len(index) == 1 and index[0].get('report_read') is True:
        return {'references': known, 'selection': 'single_report_receipt', 'version': VERSION}
    return {'references': known, 'selection': 'all_known', 'version': None}


def history_binding(messages, last_user):
    from plugins.finance.source_quotes import source_task_index
    return reference_binding(source_task_index(messages), messages[last_user].get('content'),
                             {row['source_reference'] for row in source_task_index(messages[last_user:])})
