"""Document tools bind host-owned context, never model-supplied grants."""
import hashlib
import json
import re


class BatchTransport:
    def __init__(self, client, context=None):
        self.client, self.context = client, context

    def bind(self, context):
        return BatchTransport(self.client, context)

    async def call_tool(self, tool, arguments, session_id):
        context = self.context
        if context is None or context.conversation_id != session_id or not context.run_id:
            raise PermissionError('文档处理需要宿主冻结的运行上下文')
        permission = 'workspace.read' if tool == 'documents_batch_status' else 'workspace.write'
        if permission not in context.permissions:
            raise PermissionError('工作区未授权此操作')
        if tool == 'documents_batch':
            # Validate all paths on the host before crossing the isolated boundary.
            snapshot = self.client.workspaces.snapshot(context.workspace_id)
            arguments = {**arguments, 'paths': [snapshot.virtual(context.resolve_path(path)) for path in arguments['paths']]}
            request_id = hashlib.sha256(json.dumps([context.run_id, tool, arguments], sort_keys=True).encode()).hexdigest()
            return await self.client.request(context.workspace_id, 'POST', '/tools/documents_batch', {
                'request_id': request_id, 'arguments': arguments,
                'context': {'conversation_id': context.conversation_id, 'run_id': context.run_id, 'model': context.model}}, start=True)
        if tool not in ('documents_batch_status', 'documents_batch_cancel') or set(arguments) != {'task_id'}:
            raise ValueError('无效文档任务操作')
        identity = arguments['task_id']
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('无效文档任务 ID')
        cancel = tool == 'documents_batch_cancel'
        return await self.client.request(context.workspace_id, 'POST' if cancel else 'GET',
            '/tasks/' + identity + ('/cancel' if cancel else ''), start=True)
