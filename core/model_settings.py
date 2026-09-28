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


def cloud_write_allowed():
    """Whether a cloud model may modify the workspace / run code.

    Two grant levels exist: ``workspaces`` (may read) and ``workspaces_write``
    (may also write and execute).  Settings files written before the second
    level existed have no ``workspaces_write`` key at all; those workspaces were
    granted full access, so they keep it.  As soon as the key exists the file is
    tracked by the new rules and a workspace is read-only unless explicitly
    granted writes.
    """
    key = str(config.WORKSPACE_PATH.resolve())
    settings = read_settings()
    if 'workspaces_write' not in settings:
        return key in settings.get('workspaces', {})
    return settings['workspaces_write'].get(key, False)


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


def update_settings(model_id, *, thinking=_UNSET, budget=_UNSET, workspace=None, cloud_write=_UNSET):
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
            key = str(config.WORKSPACE_PATH.resolve())
            data.setdefault('workspaces', {})[key] = workspace
            if not workspace:
                # Revoking the workspace grant also revokes the write grant, so
                # re-authorising later cannot silently restore write access.
                data.setdefault('workspaces_write', {}).pop(key, None)
        if cloud_write is not _UNSET or workspace is not None:
            # The section's presence marks the file as tracked by the two-level
            # rules, so it must exist from the first save under the new code.
            key = str(config.WORKSPACE_PATH.resolve())
            writes = data.setdefault('workspaces_write', {})
            if cloud_write is not _UNSET:
                writes[key] = bool(cloud_write)
            elif workspace:
                writes.setdefault(key, False)  # a fresh read grant is read-only
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
