"""Use the same declared or conservative context capacity in runtime and UI."""
from . import config
from copy import deepcopy


def generation_parameters(spec):
    """Effective output allowance; explicit configuration keeps its precedence."""
    if 'generation_parameters' in spec:
        return dict(spec['generation_parameters'])
    configured = spec.get('max_output_tokens') or config.MODEL_MAX_OUTPUT_TOKENS
    if type(configured) is int and configured > 0:
        return {'max_tokens': configured}
    options = spec.get('options', {})
    if 'max_tokens' in options:
        return {'max_tokens': options['max_tokens']}
    declared = spec.get('provider_max_output_tokens')
    return {'max_tokens': declared} if type(declared) is int and declared > 0 else {}


def freeze_generation_parameters(spec):
    if spec.get('kind') != 'cloud':
        return deepcopy(spec)
    return {**deepcopy(spec), 'generation_parameters': generation_parameters(spec)}


def context_capacity(spec):
    declared = spec.get('context_window') or spec.get('context_length')
    if type(declared) is int and declared >= 4096:
        return declared, 'model_catalog'
    # Honour the configured window. Capping the fallback at 32768 silently
    # floor-limited every model that did not declare one, even when .env asked
    # for a larger window.
    fallback = config.CONTEXT_WINDOW if config.CONTEXT_WINDOW and config.CONTEXT_WINDOW > 0 else 32768
    return fallback, 'conservative_fallback'
