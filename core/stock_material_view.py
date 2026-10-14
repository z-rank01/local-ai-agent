"""Compose lossless material projections independently of Agent orchestration."""
from dataclasses import dataclass

from .stock_source_quotes import prior_explanation_view, PRIOR_EXPLANATION_VIEW_VERSION
from .stock_report_context import report_material_view
from .stock_task_source_view import current_task_source_view
from .stock_task_binding import history_binding


@dataclass
class MaterialView:
    messages: list
    binding: dict | None
    priors: list
    reports: list
    other_sources: list
    prior_version: str = PRIOR_EXPLANATION_VIEW_VERSION


def project_materials(history, archive):
    """Archive before replacement; callers retain history on any failure.

    Stored user/source messages and tool arguments are not mutated. The caller
    registers all returned evidence IDs before exposing the projected view.
    """
    last_user = max((i for i, row in enumerate(history) if row.get('role') == 'user'), default=-1)
    binding = history_binding(history, last_user) if last_user >= 0 else None
    view, priors = prior_explanation_view(history, archive)
    view, reports = report_material_view(view, archive, compact_catalogues=True)
    view, other_sources = current_task_source_view(view, archive)
    return MaterialView(view, binding, priors, reports, other_sources)
