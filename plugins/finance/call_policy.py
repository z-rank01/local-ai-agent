"""Finance handoff and confirmed broker boundaries belong to the finance package."""


class FinanceCallPolicy:
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
