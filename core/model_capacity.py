"""Use the same declared or conservative context capacity in runtime and UI."""
from . import config


def context_capacity(spec):
    declared = spec.get('context_window') or spec.get('context_length')
    if type(declared) is int and declared >= 4096:
        return declared, 'model_catalog'
    return min(config.CONTEXT_WINDOW, 32768), 'conservative_fallback'
