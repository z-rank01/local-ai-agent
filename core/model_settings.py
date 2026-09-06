"""Local preferences, with workspace grants isolated by resolved host path."""
import json
import threading
from . import config

_lock = threading.RLock()

def settings_path():
    return config.PROJECT_ROOT / 'data' / 'private' / 'model-settings.json'

def read_settings():
    with _lock:
        path = settings_path()
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

def workspace_allowed():
    return read_settings().get('workspaces', {}).get(str(config.WORKSPACE_PATH.resolve()), config.WORKSPACE_CLOUD_ALLOWED)

def thinking_enabled(spec):
    return read_settings().get('models', {}).get(spec['id'], spec.get('options', {}).get('enable_thinking', False))

def update_settings(model_id, *, thinking=None, workspace=None):
    with _lock:
        data = read_settings()
        if thinking is not None:
            data.setdefault('models', {})[model_id] = thinking
        if workspace is not None:
            data.setdefault('workspaces', {})[str(config.WORKSPACE_PATH.resolve())] = workspace
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
