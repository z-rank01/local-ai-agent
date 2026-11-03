"""Parent-scoped batch coordination; no domain or model-client imports."""
import asyncio
from uuid import uuid4
from dataclasses import replace
import httpx
from harness.contracts.subagents import RoleResult, TERMINAL_SUBAGENT_STATUSES


class BatchRunner:
    def __init__(self, executor, store, *, parent_control=None, context=None, result_handler=None):
        self.executor = executor
        self.store = store
        self.parent_control = parent_control
        self.context = context
        self.result_handler = result_handler
        self._tasks = {}
        self._events = {}
        self._role_events = {}
        self._results = {}
        self._errors = {}
        self._started = set()
        self._stops = {}

    def create(self, tasks):
        batch_id = uuid4().hex
        tasks = tuple(replace(task, batch_id=batch_id) for task in tasks)
        self.store.create_batch(batch_id, tasks)
        self._events[batch_id] = asyncio.Event()
        self._role_events[batch_id] = {task.role.role_id: asyncio.Event() for task in tasks}
        self._results[batch_id] = {}
        self._tasks[batch_id] = {task.role.role_id: asyncio.create_task(self._execute(task))
                                 for task in tasks}
        if self.parent_control is not None and self.parent_control.task is not None:
            self.parent_control.task.add_done_callback(
                lambda _: asyncio.create_task(self.stop(batch_id)))
        return self.status(batch_id)

    async def _save(self, batch_id, result):
        self.store.save_result(batch_id, result)
        self._results[batch_id][result.role_id] = result
        if self.result_handler is not None:
            try:
                await self.result_handler(batch_id, result)
            except Exception as exc:
                self._errors[batch_id] = exc
                if hasattr(self.store, 'save_error'):
                    self.store.save_error(batch_id, exc)

    async def _execute(self, task):
        batch_id, role_id = task.batch_id, task.role.role_id
        try:
            for parent in task.role.depends_on:
                await self._role_events[batch_id][parent].wait()
                if batch_id in self._errors or self._results[batch_id][parent].status != 'COMPLETE':
                    result = RoleResult(role_id=role_id, status='FAILED',
                                        error='依赖角色未完成：' + parent)
                    break
            else:
                self._started.add((batch_id, role_id))
                result = await self.executor.run(task, self.context)
        except asyncio.CancelledError:
            result = RoleResult(role_id=role_id,
                status='UNKNOWN' if (batch_id, role_id) in self._started else 'STOPPED',
                error='父运行停止；已开始角色的未确认回执不自动重发')
        except httpx.TransportError as exc:
            result = RoleResult(role_id=role_id, status='UNKNOWN', error=type(exc).__name__)
        except Exception as exc:
            result = RoleResult(role_id=role_id, status='FAILED',
                                error=str(exc) or type(exc).__name__)
        try:
            await self._save(batch_id, result)
        except Exception as exc:
            self._errors[batch_id] = exc
            self._results[batch_id][role_id] = RoleResult(role_id=role_id,
                status='UNKNOWN', error='成果保存或映射失败：' + str(exc))
        finally:
            self._role_events[batch_id][role_id].set()
            if self._terminal(batch_id):
                self._events[batch_id].set()

    def _terminal(self, batch_id):
        results = self._results[batch_id]
        return (len(results) == len(self._role_events[batch_id])
                and all(result.status in TERMINAL_SUBAGENT_STATUSES for result in results.values()))

    def status(self, batch_id):
        batch = self.store.get_batch(batch_id)
        batch['complete'] = (self._terminal(batch_id) if batch_id in self._results else
            set(batch['role_ids']) == set(batch['results']) and all(
                row['status'] in TERMINAL_SUBAGENT_STATUSES for row in batch['results'].values()))
        batch['execution_active'] = any(not task.done() for task in self._tasks.get(batch_id, {}).values())
        return batch

    async def result(self, batch_id):
        event = self._events.get(batch_id)
        if event is None:
            batch = self.status(batch_id)  # Historical read, never recreate execution.
            if batch.get('postprocessing_error'):
                raise RuntimeError(batch['postprocessing_error'])
            return batch
        await event.wait()
        if batch_id in self._errors:
            raise self._errors[batch_id]
        return self.status(batch_id)

    async def stop(self, batch_id):
        if batch_id not in self._tasks:
            return self.status(batch_id)
        if batch_id not in self._stops:
            self._stops[batch_id] = asyncio.create_task(self._stop(batch_id))
        return await asyncio.shield(self._stops[batch_id])

    async def _stop(self, batch_id):
        for task in self._tasks[batch_id].values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks[batch_id].values(), return_exceptions=True)
        for role_id in self._role_events[batch_id]:
            if role_id not in self._results[batch_id]:
                try:
                    await self._save(batch_id, RoleResult(role_id=role_id, status='STOPPED',
                        error='父运行已停止；未取得的成果保留为未知'))
                except Exception as exc:
                    self._errors[batch_id] = exc
                    self._results[batch_id][role_id] = RoleResult(role_id=role_id,
                        status='UNKNOWN', error='停止成果保存失败：' + str(exc))
            self._role_events[batch_id][role_id].set()
        self._events[batch_id].set()
        if batch_id in self._errors:
            raise self._errors[batch_id]
        return self.status(batch_id)
