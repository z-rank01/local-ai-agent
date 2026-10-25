"""Minimal provider using only the published workspace execution contract."""
import hashlib
import re


class Provider:
    def invoke(self, tool, arguments, context):
        if tool != 'file_fingerprint':
            raise ValueError('Unsupported tool')
        expected = arguments.get('expected_sha256')
        if expected is not None and not re.fullmatch('[a-fA-F0-9]{64}', expected):
            raise ValueError('Expected SHA256 must contain 64 hexadecimal characters')
        path = context.resolve_path(arguments['path'])
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                size += len(chunk)
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        return {'status': 'SUCCEEDED', 'path': arguments['path'], 'bytes': size,
                'sha256': fingerprint, 'matches_expected': None if expected is None else fingerprint == expected.lower()}
