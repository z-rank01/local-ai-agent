"""Server-owned runs. Observers neither own execution nor cancel it by disconnecting."""
import asyncio
from contextlib import aclosing
from contextvars import ContextVar
import json
import logging
import uuid

from fastapi import HTTPException

from .run_store import RunStore, TERMINAL

log = logging.getLogger(__name__)
current_run = ContextVar('current_run', default=None)


class RunControl:
    def __init__(self, manager, run_id):
        self.manager, self.id = manager, run_id
        self.pause_requested = False
        self.gate = asyncio.Event()
        self.gate.set()
        self.task = None
        self.started = False
        self.subscribers = set()

    async def boundary(self, checkpoint=None):
        if checkpoint is not None:
            self.manager.store.update(self.id, checkpoint=checkpoint)
        if self.pause_requested:
            self.manager.state(self.id, 'PAUSED')
            self.gate.clear()
            await self.gate.wait()

    def emit(self, event):
        for queue in tuple(self.subscribers):
            queue.put_nowait(event)


class RunManager:
    def __init__(self, conversation_store):
        self.conversations = conversation_store
        self.store = RunStore(conversation_store)
        self.live = {}
        self.accepting = True
        self.store.interrupt_orphans()

    def public(self, row):
        if row is None:
            raise HTTPException(404, '运行不存在')
        checkpoint = row['checkpoint']
        payload = row['payload']
        return {k: v for k, v in row.items() if k not in ('payload', 'checkpoint')} | {
            'title': payload.get('title') or payload.get('message', '')[:80] or row['kind'],
            'checkpoint_available': bool(checkpoint),
            'resumable': bool(checkpoint.get('safe')),
            'resume_blocker': checkpoint.get('blocker') or ('' if checkpoint.get('safe') else '尚无安全检查点'),
        }

    def state(self, run_id, status, **extra):
        self.store.update(run_id, status=status, **extra)
        return self.publish(run_id, {'event': 'run.updated', 'data': {'run': self.public(self.store.get(run_id))}})

    def publish(self, run_id, event):
        event = dict(event)
        event['run_id'] = run_id
        # Reasoning stays in the existing live channel, never in the new journal.
        ephemeral = event['event'].startswith('reasoning.')
        if not ephemeral:
            durable = {**event, 'data': dict(event.get('data') or {})}
            if event.get('message_id') and event['event'] in ('tool.completed', 'assistant.completed'):
                for key in ('result', 'detail', 'text'):
                    durable['data'].pop(key, None)
                durable['message_reference'] = True
            event = self.store.append(run_id, durable)
        control = self.live.get(run_id)
        if control:
            control.emit(event)
        return event

    def hydrate(self, event):
        if not event.get('message_reference'):
            return event
        row = self.store.get(event['run_id'])
        messages = self.conversations.get_messages(row['conversation_id'], include_inactive=True)
        message = next((m for m in messages if m.id == event['message_id']), None)
        if message is None:
            raise RuntimeError('运行事件引用的消息不存在')
        data = dict(event['data'])
        if event['event'] == 'tool.completed':
            data['result'] = json.loads(message.tool_result) if message.tool_result else None
            data['detail'] = message.content
        else:
            data['text'] = message.content
        return {**event, 'data': data}

    def start(self, *, workspace_id, conversation_id, request_id, kind, payload, factory, resume_of=None):
        existing = self.store.by_request(request_id)
        if existing:
            if existing['workspace_id'] != workspace_id or existing['payload'] != payload:
                raise HTTPException(409, '请求标识已经用于另一份输入')
            return existing['id']
        if not self.accepting:
            raise HTTPException(409, '正在退出，暂不接受新运行')
        run_id = uuid.uuid4().hex[:12]
        self.store.create(run_id, workspace_id=workspace_id, conversation_id=conversation_id,
                          request_id=request_id, kind=kind, payload=payload, resume_of=resume_of)
        control = RunControl(self, run_id)
        self.live[run_id] = control
        control.task = asyncio.create_task(self._produce(control, factory), name='chat-run-'+run_id)
        def unstarted_cancel(task):
            if task.cancelled() and not control.started:
                self.state(run_id, 'STOPPED', error='用户在开始执行前停止')
                self.live.pop(run_id, None)
                control.emit(None)
        control.task.add_done_callback(unstarted_cancel)
        return run_id

    async def _produce(self, control, factory):
        control.started = True
        token = current_run.set(control)
        failed = False
        try:
            async with aclosing(factory()) as events:
                async for event in events:
                    value = event.model_dump()
                    value['run_id'] = control.id
                    if value.get('conversation_id'):
                        self.store.update(control.id, conversation_id=value['conversation_id'])
                    if value['event'] == 'model.selected':
                        self.store.update(control.id, model=value['data']['model'])
                    if value['event'] in ('error', 'session.error'):
                        failed = True
                    if value['event'] == 'tool.started':
                        self.store.update(control.id, phase=value['data'].get('summary') or value['data'].get('name', '工具执行'))
                    self.publish(control.id, value)
            self.state(control.id, 'FAILED' if failed else 'SUCCEEDED')
        except asyncio.CancelledError:
            self.state(control.id, 'STOPPED', error='用户已停止；已取得成果保留')
        except Exception as exc:
            log.exception('Run %s failed', control.id)
            detail = str(exc.detail) if isinstance(exc, HTTPException) else (
                str(exc) if isinstance(exc, (ValueError, RuntimeError, PermissionError)) else type(exc).__name__)
            self.publish(control.id, {'event': 'error', 'data': {'message': detail}})
            self.state(control.id, 'FAILED', error=detail)
        finally:
            current_run.reset(token)
            self.live.pop(control.id, None)
            control.emit(None)

    async def subscribe(self, run_id, after_seq=0):
        if self.store.get(run_id) is None:
            raise HTTPException(404, '运行不存在')
        queue = asyncio.Queue()
        control = self.live.get(run_id)
        if control:
            control.subscribers.add(queue)
        cursor = after_seq
        try:
            for event in self.store.events(run_id, cursor):
                cursor = event['seq']
                yield self.hydrate(event)
            if self.store.get(run_id)['status'] in TERMINAL:
                return
            while control:
                event = await queue.get()
                if event is None:
                    return
                sequence = event.get('seq')
                if sequence is not None and sequence <= cursor:
                    continue
                cursor = sequence if sequence is not None else cursor
                yield self.hydrate(event)
        finally:
            if control:
                control.subscribers.discard(queue)

    async def action(self, run_id, action):
        row = self.store.get(run_id)
        if row is None:
            raise HTTPException(404, '运行不存在')
        control = self.live.get(run_id)
        if control is None:
            if action == 'stop' and row['status'] in TERMINAL:
                return self.public(row)
            raise HTTPException(409, '运行已结束；请核对检查点后选择继续')
        if action == 'pause':
            control.pause_requested = True
            if row['status'] != 'PAUSED':
                self.state(run_id, 'PAUSING')
        elif action == 'resume':
            if row['status'] not in ('PAUSED', 'PAUSING'):
                raise HTTPException(409, '当前运行没有暂停')
            control.pause_requested = False
            self.state(run_id, 'RUNNING')
            control.gate.set()
        elif action == 'stop':
            self.state(run_id, 'STOPPING')
            control.task.cancel()
            await asyncio.gather(control.task, return_exceptions=True)
        else:
            raise HTTPException(422, '未知运行操作')
        return self.public(self.store.get(run_id))

    async def close(self):
        self.accepting = False
        tasks = [control.task for control in tuple(self.live.values())]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
