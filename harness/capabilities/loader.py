"""Load trusted service adapters from manifests without changing the Agent."""
import importlib
import logging
import os

from harness.contracts.plugin_manifest import read_manifest


def register_adapters(runtime, packages):
    services, errors = {}, {}
    for manifest in sorted(packages.glob('*/plugin.json')):
        identity = manifest.parent.name
        try:
            metadata = read_manifest(manifest, packages)
            reference = metadata.get('adapter')
            if not reference:
                continue
            module, separator, name = reference.partition(':')
            if not separator or not module.startswith('plugins.' + identity + '.') or not name.isidentifier():
                raise ValueError('服务适配器必须属于当前插件')
            service = getattr(importlib.import_module(module), name)
            service.lifecycle(runtime)
            services[identity] = service
        except (OSError, ValueError, TypeError, AttributeError, ImportError) as exc:
            errors[identity] = str(exc) or type(exc).__name__
            logging.getLogger(__name__).exception('Plugin adapter registration failed: %s', identity)
    runtime.capability_services = services
    runtime.adapter_errors = errors
    return services


async def restore_adapters(runtime, services):
    if os.environ.get('DAILY_SERVICES') != '1':
        return
    for identity, service in services.items():
        try:
            await service.restore(runtime)
        except Exception as exc:
            runtime.adapter_errors[identity] = str(exc) or type(exc).__name__
            logging.getLogger(__name__).exception('Plugin adapter restore failed: %s', identity)
