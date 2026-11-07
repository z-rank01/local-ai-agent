from harness.infrastructure.capability_store import CapabilityStore


async def set_managed_enabled(runtime, identity, enabled):
    manager = runtime.capabilities
    if manager is None or not manager.manages(identity):
        raise ValueError('插件尚未加载，请通过唯一入口重启')
    store = CapabilityStore(runtime.store._connect)
    store.save('__host__', identity, {'enabled':enabled},'plugin')
    if not enabled:
        return await manager.deactivate(identity)
    dependencies = {k for k,v in store.list('__host__','plugin').items() if v.get('enabled')}
    return await manager.activate(identity,enabled_dependencies=dependencies)
