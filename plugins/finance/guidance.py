"""Compose financial calculation, comparison and reading coverage context."""
from dataclasses import dataclass

from plugins.finance.calculation_review import current_program_records, program_source_guidance
from plugins.finance.company_context import current_guidance, current_cashflow_scope_version
from plugins.finance.source_coverage import coverage_guidance


@dataclass
class FinancialGuidance:
    calculations: list
    content: str
    cashflow_scope_version: str | None
    has_comparison: bool = False
    has_coverage: bool = False


def compose_guidance(calculations, comparisons, question, sources, reads, *,
                     authorized, messages, evidence_ids):
    current = current_program_records(calculations, question, sources)
    if not authorized:
        return FinancialGuidance(current, '', None)
    comparison = current_guidance(comparisons, question, sources, reads,
                                  visible_messages=messages, evidence_ids=evidence_ids)
    coverage = coverage_guidance(reads, question, sources)
    parts = (program_source_guidance(current), comparison, coverage)
    scope = current_cashflow_scope_version(comparisons, question, sources) if comparison else None
    return FinancialGuidance(current, '\n\n'.join(part for part in parts if part), scope,
                             bool(comparison), bool(coverage))
