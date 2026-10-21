"""One workspace-owned job record; outputs and originals are not copied into a DB."""
import hashlib
import json
import re
import os
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


class Records:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('Invalid job identity')
        return self.directory / (identity + '.json')

    def save(self, record):
        record['updated_at'] = datetime.now(timezone.utc).isoformat()
        path = self.path(record['id'])
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False, indent=2))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)

    def get(self, identity):
        return json.loads(self.path(self.resolve(identity)).read_text(encoding='utf-8'))

    def resolve(self, identity):
        if isinstance(identity, str) and re.fullmatch('[a-f0-9]{16}', identity):
            matches = [path.stem for path in self.directory.glob(identity + '*.json')
                       if re.fullmatch('[a-f0-9]{64}', path.stem)]
            if not matches:
                raise FileNotFoundError('Task reference not found')
            if len(matches) != 1:
                raise ValueError('任务短引用不唯一，请使用完整任务 ID')
            return matches[0]
        self.path(identity)  # Full identities keep the same validation and storage.
        return identity

    def all(self):
        return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(self.directory.glob('*.json'))]

    def recover(self):
        for record in self.all():
            if record['state'] in ('QUEUED', 'RUNNING'):
                record['state'] = 'INTERRUPTED'
                record['error'] = '服务在终态确认前退出；未自动重发。请核对已有输出后明确提交新任务。'
                for item in record['files']:
                    if item['state'] == 'RUNNING':
                        item['state'] = 'UNKNOWN'
                self.save(record)
