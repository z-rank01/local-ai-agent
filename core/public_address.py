"""URL syntax guard; the stock original reader also pins validated DNS addresses."""
import ipaddress
from urllib.parse import urlsplit


def public_url(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or '\\' in url or any(ord(c) < 32 for c in url)):
            raise ValueError('Non-public URL')
        host = parsed.hostname.lower().rstrip('.')
        if '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan')):
            raise ValueError('Local hostname')
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and not ip.is_global:
            raise ValueError('Non-public IP')
        return parsed
    except (TypeError, AttributeError):
        raise ValueError('Invalid URL') from None
