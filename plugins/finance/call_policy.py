"""Finance handoff and confirmed broker boundaries belong to the finance package."""


class FinanceCallPolicy:
    def accepted_handoff(self, tool, receipt):
        return isinstance(receipt, dict) and receipt.get('code') == 'TASK_SUBMITTED'

    def phase_guidance(self, phase):
        from .phase_guidance import HANDOFF
        return HANDOFF if phase == 'handoff' else ''

    def present_receipt(self, tool, arguments, receipt, *, question, read_receipts):
        from harness.contracts.receipt_presentation import ReceiptPresentation
        if tool == 'stock_public_evidence_read':
            from .source_coverage import scope_reply
            text = scope_reply(receipt, arguments, question, read_receipts)
            return ReceiptPresentation(text, 'source_scope_reply', 'source-scope-reply-v1') if text else None
        if tool == 'stock_report_read':
            from .arithmetic_purpose import calculation_reply
            text = calculation_reply(receipt, arguments, question)
            return ReceiptPresentation(text, 'program_calculation_reply', 'program-arithmetic-reply-v1') if text else None
        return None

    def project_history(self, history, archive):
        from .history import project_history
        return project_history(history, archive)

    def validate(self, tool, arguments, *, phase):
        if phase != 'handoff':
            return
        if tool == 'stock_research_submit' and arguments.get('research_mode', 'reuse') not in ('reuse', 'continue'):
            raise ValueError('后台交接只能复用研究，不能以新版本绕过未知调用。')
        if tool in ('stock_theme_research_submit', 'stock_research_submit') and not str(arguments.get('research_question') or '').strip():
            raise ValueError('后台交接必须明确传入公开研究问题，不能丢失原任务目标。')

    def ends_window(self, tool, receipt):
        return isinstance(receipt, dict) and (
            receipt.get('status') in ('TIME_LIMIT', 'STEP_LIMIT') or receipt.get('broker_continue') is False)
