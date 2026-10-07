"""Launcher-created browser credential, kept outside all model workspaces."""
import secrets
from . import config


def session_token():
    path = config.PROJECT_ROOT / 'data/private/web-session.token'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as file:
            file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    value = path.read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError('页面会话凭据无效，请通过启动.bat重新启动')
    return value


def authenticated(request):
    value = request.headers.get('X-Harness-Session') or request.cookies.get('harness_session', '')
    return secrets.compare_digest(value.encode(), session_token().encode())
