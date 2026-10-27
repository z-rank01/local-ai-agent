"""Register every bundled provider before restoring saved activation intent."""
from harness.capabilities.loader import register_adapters, restore_adapters
from harness.capabilities.packaged_plugins import register_packages
from harness.capabilities.container_plugins import register_containers
from harness.infrastructure.capability_store import CapabilityStore
from harness.runtime.commands import CommandRegistry


async def restore_plugins(runtime, packages, endpoint, headers):
    runtime.commands = CommandRegistry.bundled(packages)
    services = register_adapters(runtime, packages)
    register_packages(runtime, packages, endpoint, headers)
    register_containers(runtime, packages)
    await restore_adapters(runtime, services)
    states = CapabilityStore(runtime.store._connect).list('__host__', 'plugin')
    enabled = {identity for identity, state in states.items() if state.get('enabled')}
    enabled.update(identity for identity in services
                   if runtime.capabilities.status(identity)['accepting'])
    for identity, state in states.items():
        if state.get('enabled') and runtime.capabilities.manages(identity):
            try:
                await runtime.capabilities.activate(identity, enabled_dependencies=enabled)
            except Exception:
                # Lifecycle retains the failure; unrelated capabilities and chat remain usable.
                pass
