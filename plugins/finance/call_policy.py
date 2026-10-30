"""Finance receipt presentation and confirmed legacy broker boundaries."""


class FinanceCallPolicy:
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
        # Authorization, UNKNOWN isolation and domain parameters are enforced by
        # the existing router/broker, without a timer-created handoff phase.
        return

    def ends_window(self, tool, receipt):
        return isinstance(receipt, dict) and (
            receipt.get('status') in ('TIME_LIMIT', 'STEP_LIMIT') or receipt.get('broker_continue') is False)

    def normalize_publication(self, params):
        from .publication import normalize_publication
        return normalize_publication(params)

    def literal_text(self, text):
        from .publication import literal_text
        return literal_text(text)

    def validate_publication(self, params, sources, read_text):
        from .publication import validate_publication
        return validate_publication(params, sources, read_text)

    def render_charts(self, charts):
        from .publication import render_charts
        return render_charts(charts)

    def validate_visual_plan(self, params):
        from .publication import validate_visual_plan
        return validate_visual_plan(params)
