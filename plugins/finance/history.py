"""Finance report/source projection and its provenance annotations."""
from harness.contracts.history_projection import HistoryProjection
from .material_view import project_materials
from core.stock_report_context import MATERIAL_VIEW_VERSION


def project_history(history, archive):
    result = project_materials(history, archive)
    entries = result.priors + result.reports + result.other_sources
    if not entries:
        return HistoryProjection(history)
    binding = result.binding
    version = MATERIAL_VIEW_VERSION if result.reports else 'prior-explanation-index-v2'
    prior_version = result.prior_version if result.priors else None
    return HistoryProjection(result.messages, {row['evidence_id'] for row in entries},
        {'view_version': version, 'prior_version': prior_version,
         'source_view': bool(result.other_sources)},
        {'version': version, 'prior_explanation_index_version': prior_version,
         'task_material_binding_version': binding['version'] if binding else None,
         'task_material_binding_selection': binding['selection'] if binding else None,
         'task_material_binding_count': len(binding['references']) if binding else 0,
         'indexed_messages': len(entries), 'indexed_report_materials': len(result.reports),
         'indexed_other_task_source_receipts': len(result.other_sources) - result.duplicate_count,
         'indexed_duplicate_source_receipts': result.duplicate_count,
         'before_chars': sum(len(row.get('content') or '') for row in history),
         'after_chars': sum(len(row.get('content') or '') for row in result.messages)})
