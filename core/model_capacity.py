"""Use the same declared or conservative context capacity in runtime and UI."""
from . import config


def context_capacity(spec):
    declared = spec.get('context_window') or spec.get('context_length')
    if type(declared) is int and declared >= 4096:
        return declared, 'model_catalog'
    # Honour the configured window. Capping the fallback at 32768 silently
    # floor-limited every model that did not declare one, even when .env asked
    # for a larger window.
    fallback = config.CONTEXT_WINDOW if config.CONTEXT_WINDOW and config.CONTEXT_WINDOW > 0 else 32768
    return fallback, 'conservative_fallback'
