"""Run-owned ToolTransport for one-level subagent batches."""
from .batch import BatchRunner
from .spec import role_tasks
from harness.runtime.evidence import fingerprint


def _view(batch):
    result = dict(batch)
    result['results'] = {}
    for role_id, row in batch['results'].items():
        receipt = dict(row.get('native_receipt') or {})
        receipt['tools'] = [{**{key: tool.get(key) for key in ('name', 'call_id', 'status')},
                             'result_sha256':fingerprint(tool.get('result'))}
                            for tool in receipt.get('tools', [])]
        result['results'][role_id] = {key:value for key, value in
            {**row, 'native_receipt':receipt}.items() if key != 'raw_text'}
    return result


class SubagentTransport:
    def __init__(self, roles=None, executor=None, store=None, parent_control=None, context=None):
        self.roles = roles or {}
        self.executor = executor
        self.store = store
        self.parent_control = parent_control
        self.context = context
        self._batch_runner = None

    def bind(self, context):
        return SubagentTransport(self.roles, context.subagent_executor,
                                 context.subagent_store, context.run_control, context)

    async def _record_result(self, batch_id, result):
        research = self.context.research
        if research is None or not research.active or research.record.get('research_contract_version') != 3:
            return
        stored = self.store.get_batch(batch_id)['results'][result.role_id]
        await research.record_role_result(batch_id, stored)

    def _runner(self):
        if self.executor is None or self.store is None:
            raise RuntimeError('子Agent需要当前聊天运行上下文')
        if self._batch_runner is None:
            self._batch_runner = BatchRunner(self.executor, self.store,
                parent_control=self.parent_control, context=self.context,
                result_handler=self._record_result)
        return self._batch_runner

    async def call_tool(self, tool, arguments, session_id):
        runner = self._runner()
        if tool == 'agent_batch_create':
            tasks = role_tasks(arguments.get('tasks'), self.roles)
            return runner.create(tasks)
        batch_id = arguments.get('batch_id')
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError('须返回实际batch_id')
        if tool == 'agent_batch_status':
            return _view(runner.status(batch_id))
        if tool == 'agent_batch_result':
            result = await runner.result(batch_id)
            research = self.context.research
            if research is not None and hasattr(research, 'role_batch_summary'):
                summary = research.role_batch_summary(batch_id)
                if summary is not None:
                    result['research_roles'] = summary
            return _view(result)
        if tool == 'agent_batch_stop':
            return _view(await runner.stop(batch_id))
        raise ValueError('未登记的子Agent工具')
