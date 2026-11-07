"""Finance projections for the generic parent-owned subagent runtime."""
from harness.runtime.subagents.extensions import NullSubagentExtension
from harness.runtime.turns import NullTurn


class SourceTurn(NullTurn):
    def __init__(self, extension):
        super().__init__()
        self.extension = extension

    def before_tool(self, tool, arguments):
        if tool == 'web_fetch' and self.extension.research is not None:
            return {**arguments, 'include_file': True}
        return arguments

    async def after_tool(self, tool, result):
        research = self.extension.research
        if tool == 'web_fetch' and research is not None:
            return await research.save_source(result)
        return result


class FinanceSubagentExtension(NullSubagentExtension):
    def __init__(self, turn):
        self.turn = turn

    @property
    def research(self):
        research = self.turn.research
        return research if research is not None and research.active else None

    def material(self, task, prior_results):
        research = self.research
        if research is None or (getattr(research, 'record', None) or {}).get('research_contract_version') != 3:
            return super().material(task, prior_results)
        # Validated domain artifacts replace the duplicate raw role output.
        from .research.material_view import role_material_view
        material = role_material_view(research, task.batch_id)
        selected = {path:body for path,body in getattr(research,'method_text',{}).items() if body}
        if selected:
            material['selected_method_notes'] = selected
        return {'recipe_artifacts': material, 'prior_results': {
            role: {key: row[key] for key in ('status', 'issues', 'result_ref') if key in row}
            for role, row in prior_results.items()}}

    def tool_turn(self, task):
        return SourceTurn(self)

    async def observe_tool(self, task, event):
        if self.research is not None:
            await self.research.observe({**event, 'subagent': True, 'role_id': task.role.role_id})

    async def saved_result(self, batch_id, result):
        research = self.research
        if research is not None and research.record.get('research_contract_version') == 3:
            await research.record_role_result(batch_id, result)

    def batch_summary(self, batch_id):
        summary = self.research.role_batch_summary(batch_id) if self.research is not None else None
        return {'research_roles': summary} if summary is not None else {}
