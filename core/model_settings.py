"""Local preferences, with workspace grants isolated by resolved host path."""
import json
import threading
from . import config

_lock = threading.RLock()
_UNSET = object()


def settings_path():
    return config.PROJECT_ROOT / 'data' / 'private' / 'model-settings.json'


def read_settings():
    with _lock:
        path = settings_path()
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def workspace_allowed():
    return read_settings().get('workspaces', {}).get(str(config.WORKSPACE_PATH.resolve()), config.WORKSPACE_CLOUD_ALLOWED)


def thinking_setting(spec):
    """Explicit thinking switch for a model: True / False, or None (= default).

    None means the harness does not interfere: catalog-declared options are sent
    as-is for static entries, and nothing is sent for discovered models (the
    provider default applies, which for hybrid models is usually thinking-on).
    """
    value = read_settings().get('models', {}).get(spec['id'])
    return None if value is None else bool(value)


def thinking_enabled(spec):
    """Effective switch state: explicit setting, else catalog-declared default, else False."""
    value = thinking_setting(spec)
    if value is not None:
        return value
    return spec.get('options', {}).get('enable_thinking', False)


def thinking_budget(model_id):
    value = read_settings().get('thinking_budgets', {}).get(model_id)
    return value if isinstance(value, int) and value > 0 else None


def update_settings(model_id, *, thinking=_UNSET, budget=_UNSET, workspace=None):
    with _lock:
        data = read_settings()
        if thinking is not _UNSET:
            models = data.setdefault('models', {})
            if thinking is None:
                models.pop(model_id, None)
            else:
                models[model_id] = bool(thinking)
        if budget is not _UNSET:
            budgets = data.setdefault('thinking_budgets', {})
            if budget is None:
                budgets.pop(model_id, None)
            else:
                budgets[model_id] = int(budget)
        if workspace is not None:
            data.setdefault('workspaces', {})[str(config.WORKSPACE_PATH.resolve())] = workspace
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
