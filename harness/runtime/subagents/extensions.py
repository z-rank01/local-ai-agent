"""No-op subagent contribution for ordinary, non-domain conversations."""
from harness.runtime.turns import NullTurn


class NullSubagentExtension:
    def material(self, task, prior_results):
        return {'prior_results': prior_results} if prior_results else {}

    def tool_turn(self, task):
        return NullTurn()

    async def observe_tool(self, task, event):
        pass

    async def saved_result(self, batch_id, result):
        pass

    def batch_summary(self, batch_id):
        return {}
