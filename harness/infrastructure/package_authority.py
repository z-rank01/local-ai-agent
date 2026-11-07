"""Host-issued package grants. Signatures cover the complete package revision."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def signed_message(identity, revision):
    return f'local-ai-agent/package/v1\n{identity}\n{revision}\n'.encode('utf-8')


def valid_identity(identity, kind):
    pattern = r'[\w-]{1,128}' if kind == 'skill' else r'[a-z][a-z0-9-]{0,47}'
    return isinstance(identity, str) and re.fullmatch(pattern, identity) is not None


def verify_package(grant, identity, revision, signature, permissions):
    if not set(permissions) <= set(grant['permissions']):
        raise PermissionError('包请求了来源授权范围以外的权限')
    if not re.fullmatch('[0-9a-f]{128}',signature or ''):
        raise PermissionError('需要已授权来源的 Ed25519 版本签名')
    openssl = Path(sys.prefix)/'Library/bin/openssl.exe'
    if not openssl.is_file():
        raise ValueError('宿主 OpenSSL 不可用，无法验证发布者')
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root/'key').write_bytes(bytes.fromhex('302a300506032b6570032100'+grant['public_key']))
        (root/'sig').write_bytes(bytes.fromhex(signature))
        (root/'message').write_bytes(signed_message(identity,revision))
        check = subprocess.run([str(openssl),'pkeyutl','-verify','-pubin','-inkey','key',
            '-keyform','DER','-rawin','-in','message','-sigfile','sig'],cwd=root,
            capture_output=True,timeout=10)
        if check.returncode:
            raise PermissionError('发布者签名与实际包版本不匹配')
