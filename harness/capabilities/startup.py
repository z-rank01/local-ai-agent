"""Register every bundled provider before restoring saved activation intent."""
from harness.capabilities.packaged_plugins import register_packages
from harness.capabilities.container_plugins import register_containers
from harness.infrastructure.capability_store import CapabilityStore


async def restore_plugins(runtime, packages, endpoint, headers):
    register_packages(runtime, packages, endpoint, headers)
    register_containers(runtime, packages)
    states = CapabilityStore(runtime.store._connect).list('__host__', 'plugin')
    enabled = {identity for identity, state in states.items() if state.get('enabled')}
    for identity, state in states.items():
        if state.get('enabled') and runtime.capabilities.manages(identity):
            try:
                await runtime.capabilities.activate(identity, enabled_dependencies=enabled)
            except Exception:
                # Lifecycle retains the failure; unrelated capabilities and chat remain usable.
                pass
