"""Desktop window host for the daily Web UI.

Wraps the same BFF page (http://127.0.0.1:9510) in a native pywebview
window instead of a browser tab. No backend change: the page keeps
sending its heartbeat, so closing the window triggers the existing
auto-exit (stock graceful stop, tool containers, then the BFF itself).

Usage: desktop_window.py <url>   (url must point at the local BFF)
Exits non-zero when pywebview or its runtime is unavailable so the
launcher can fall back to the browser.
"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

ALLOWED_HOSTS = {'127.0.0.1', 'localhost'}
ALLOWED_PORTS = {9510}


def validate_url(url: str) -> str:
    """Return the URL unchanged, or raise ValueError if it is not the local BFF."""
    parts = urlsplit(url)
    if parts.scheme != 'http':
        raise ValueError(f'only http URLs are allowed, got: {parts.scheme or url!r}')
    if parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(f'only loopback hosts are allowed, got: {parts.hostname!r}')
    if parts.port not in ALLOWED_PORTS:
        raise ValueError(f'only the daily BFF port is allowed, got: {parts.port!r}')
    if parts.path not in ('', '/'):
        raise ValueError(f'unexpected path in BFF URL: {parts.path!r}')
    return url


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: desktop_window.py <url>', file=sys.stderr)
        return 2
    try:
        url = validate_url(argv[0])
    except ValueError as exc:
        print(f'desktop window refused the URL: {exc}', file=sys.stderr)
        return 2
    try:
        import webview
    except ImportError:
        print('pywebview is not installed; falling back to the browser', file=sys.stderr)
        return 3
    try:
        webview.create_window('Local AI Agent', url, width=1440, height=900, min_size=(960, 600))
        webview.start()
    except Exception as exc:
        print(f'desktop window could not start: {exc}', file=sys.stderr)
        return 4
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
