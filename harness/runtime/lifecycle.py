"""Serialize lifecycle changes and drain calls without changing their results."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from harness.contracts.lifecycle import Lifecycle


@dataclass
class Instance:
    provider: Lifecycle
    required: tuple[str, ...] = ()
    state: str = 'INACTIVE'
    error: str = ''
    calls: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self):
        self.idle.set()


class LifecycleManager:
    def __init__(self):
        self._instances: dict[str, Instance] = {}
        self._aliases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def register(self, identity: str, provider: Lifecycle, *, required=(), aliases=()):
        names = (identity, *aliases)
        if len(set(names)) != len(names) or any(not name or self.manages(name) for name in names):
            raise ValueError(f'能力名称或别名已登记/无效：{identity}')
        if set(names).intersection(required):
            raise ValueError('能力不能依赖自身')
        self._instances[identity] = Instance(provider, tuple(required))
        self._aliases.update({alias: identity for alias in aliases})

    def _identity(self, identity):
        return self._aliases.get(identity, identity)

    def status(self, identity):
        instance = self._instances[self._identity(identity)]
        return {'state': instance.state, 'error': instance.error, 'inflight': instance.calls,
                'required': list(instance.required), 'accepting': instance.state == 'ACTIVE'}

    def manages(self, identity):
        return self._identity(identity) in self._instances

    async def activate(self, identity):
        async with self._lock:
            await self._activate(identity, ())
        return self.status(identity)

    async def _activate(self, identity, ancestors):
        identity = self._identity(identity)
        if identity in ancestors:
            raise ValueError('能力依赖形成循环：' + ' → '.join((*ancestors, identity)))
        if identity not in self._instances:
            raise ValueError(f'缺少能力依赖：{identity}')
        instance = self._instances[identity]
        if instance.state == 'ACTIVE':
            return
        if instance.state in ('DRAINING', 'STOP_FAILED'):
            raise ValueError('先完成能力收尾，再重新激活')
        instance.state, instance.error = 'STARTING', ''
        try:
            for dependency in instance.required:
                await self._activate(dependency, (*ancestors, identity))
            await instance.provider.activate()
            instance.state = 'ACTIVE'
        except BaseException as exc:
            instance.state, instance.error = 'START_FAILED', str(exc) or type(exc).__name__
            raise

    @asynccontextmanager
    async def call(self, identity):
        instance = self._instances[self._identity(identity)]
        if instance.state != 'ACTIVE':
            raise RuntimeError(f'能力不接收新调用：{identity} ({instance.state})')
        # No await between admission and increment, so deactivation cannot interleave.
        instance.calls += 1
        instance.idle.clear()
        try:
            yield
        finally:
            instance.calls -= 1
            if not instance.calls:
                instance.idle.set()

    async def deactivate(self, identity, *, timeout=30, reconcile=False):
        identity = self._identity(identity)
        async with self._lock:
            instance = self._instances[identity]
            if instance.state == 'INACTIVE' and not reconcile:
                return self.status(identity)
            dependents = [name for name, other in self._instances.items()
                          if any(self._identity(dependency) == identity for dependency in other.required)
                          and other.state in ('ACTIVE', 'DRAINING', 'STOP_FAILED')]
            if dependents:
                raise ValueError('请先停用依赖此能力的插件：' + ', '.join(dependents))
            instance.state, instance.error = 'DRAINING', ''
            try:
                await asyncio.wait_for(instance.idle.wait(), timeout)
                await instance.provider.deactivate()
            except BaseException as exc:
                instance.state, instance.error = 'STOP_FAILED', str(exc) or type(exc).__name__
                raise
            instance.state = 'INACTIVE'
            return self.status(identity)
