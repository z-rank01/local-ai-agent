"""Request-local capture hook, invoked on serialized visible provider input.

The host supplies storage and policy. Providers know nothing about domain stages.
"""
from contextvars import ContextVar

material_observer = ContextVar('request_material_observer', default=None)


def observe_materials(messages, metadata):
    observer = material_observer.get()
    return {'material_manifest': observer(messages, metadata)} if observer else {}
