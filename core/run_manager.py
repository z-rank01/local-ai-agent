"""Server-owned runs. Observers neither own execution nor cancel it by disconnecting."""
import asyncio
from contextlib import aclosing
from contextvars import ContextVar
import json
import logging
import uuid
import httpx

from fastapi import HTTPException

from .run_store import RunStore, TERMINAL

log = logging.getLogger(__name__)
current_run = ContextVar('current_run', default=None)


class RunControl:
    def __init__(self, manager, run_id):
        self.manager, self.id = manager, run_id
        self.pause_requested = False
        self.stop_requested = False
        self.cleanup_check = None
        self.cleanup_ready = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.task = None
        self.started = False
        self.subscribers = set()
        self.checkpoint_factory = None
        self.validate = None
        self.blocker = ''

    async def boundary(self, checkpoint=None):
        if self.stop_requested:
            raise asyncio.CancelledError()
        if self.checkpoint_factory is not None and checkpoint is not None:
            checkpoint = self.checkpoint_factory(checkpoint)
            checkpoint.update(safe=not bool(self.blocker), blocker=self.blocker)
        if checkpoint is not None:
            self.manager.store.update(self.id, checkpoint=checkpoint)
        if self.pause_requested:
            self.manager.state(self.id, 'PAUSED')
            self.gate.clear()
            await self.gate.wait()

    def begin_step(self, kind):
        checkpoint = self.manager.store.get(self.id)['checkpoint']
        checkpoint.update(safe=False, blocker=kind+'尚无确认终态')
        self.manager.store.update(self.id, checkpoint=checkpoint, phase=kind)

    def end_step(self, *, confirmed=True):
        if not confirmed:
            self.blocker = '存在未确认的模型或工具结果，不能自动重跑'
            checkpoint = self.manager.store.get(self.id)['checkpoint']
            checkpoint.update(safe=False, blocker=self.blocker)
            self.manager.store.update(self.id, checkpoint=checkpoint)

    def emit(self, event):
        for queue in tuple(self.subscribers):
            queue.put_nowait(event)

    async def wait_for_cleanup(self, confirm):
        self.cleanup_check = confirm
        self.blocker = '工具进程停止未获确认；执行锁保留，请检查工具服务后重试停止'
        self.manager.state(self.id, 'STOPPING', error=self.blocker)
        await self.cleanup_ready.wait()


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
        safe = bool(checkpoint.get('safe')) and row['status'] != 'SUCCEEDED' and not checkpoint.get('continued_by')
        research = checkpoint.get('research') or {}
        published = checkpoint.get('published')
        return {k: v for k, v in row.items() if k not in ('payload', 'checkpoint')} | {
            'title': payload.get('title') or payload.get('message', '')[:80] or row['kind'],
            'checkpoint_available': bool(checkpoint),
            'resumable': safe,
            'resume_blocker': checkpoint.get('blocker') or ('' if checkpoint.get('safe') else '尚无安全检查点'),
            'research_status': (research.get('record') or {}).get('status'),
            'publication_status': 'PUBLISHED' if published else ('NOT_PUBLISHED' if research else None),
            'artifacts': [published] if published else [],
        }

    def state(self, run_id, status, **extra):
        self.store.update(run_id, status=status, **extra)
        return self.publish(run_id, {'event': 'run.updated', 'data': {'run': self.public(self.store.get(run_id))}})

    def publish(self, run_id, event):
        event = dict(event)
        event['run_id'] = run_id
        # Reasoning stays in the existing live channel, never in the new journal.
        # Reconnect uses durable step events and original completed messages.
        # Token/output chunks are live-only, avoiding a second transcript and a DB write per token.
        ephemeral = event['event'].startswith('reasoning.') or event['event'] in ('assistant.delta','tool.progress')
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
        message = self.conversations.get_message(event['message_id'])
        if message is None or message.conversation_id != row['conversation_id']:
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
                    if value['event'] == 'tool.completed' and value['data'].get('name') == 'brief_publish':
                        result = value['data'].get('result') or {}
                        if value['data'].get('status') == 'ok' and isinstance(result,dict) and result.get('status') == 'PUBLISHED':
                            checkpoint = self.store.get(control.id)['checkpoint']
                            checkpoint['published'] = {key:result[key] for key in ('path','title','sha256')}
                            self.store.update(control.id, checkpoint=checkpoint)
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
        if control.stop_requested and action != 'stop':
            raise HTTPException(409, '运行正在停止，不能再次暂停或继续')
        if action == 'pause':
            control.pause_requested = True
            if row['status'] != 'PAUSED':
                self.state(run_id, 'PAUSING')
        elif action == 'resume':
            if row['status'] == 'RUNNING' and not control.pause_requested:
                return self.public(row)
            if row['status'] not in ('PAUSED', 'PAUSING'):
                raise HTTPException(409, '当前运行没有暂停')
            if control.validate:
                control.validate()
            control.pause_requested = False
            self.state(run_id, 'RUNNING')
            control.gate.set()
        elif action == 'stop':
            self.request_stop(control)
            if control.cleanup_check:
                try:
                    await control.cleanup_check()
                except (httpx.HTTPError, RuntimeError):
                    raise HTTPException(409, control.blocker) from None
                control.cleanup_check = None
                control.cleanup_ready.set()
            await asyncio.wait({control.task}, timeout=30)
        else:
            raise HTTPException(422, '未知运行操作')
        return self.public(self.store.get(run_id))

    def request_stop(self, control):
        if not control.stop_requested:
            control.stop_requested = True
            self.state(control.id, 'STOPPING')
            control.task.cancel()

    async def close(self):
        self.accepting = False
        tasks = [control.task for control in tuple(self.live.values())]
        for control in tuple(self.live.values()):
            self.request_stop(control)
            if control.cleanup_check:
                try:
                    await control.cleanup_check()
                except (httpx.HTTPError, RuntimeError) as exc:
                    raise RuntimeError(control.blocker) from exc
                control.cleanup_check = None
                control.cleanup_ready.set()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=30)
            if pending:
                raise RuntimeError('运行仍在收尾；尚未确认停止，保留执行锁与服务')
            results = await asyncio.gather(*done, return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException) and not isinstance(result,asyncio.CancelledError):
                    raise RuntimeError('任务收尾失败，不能确认整栈已安全退出') from result
