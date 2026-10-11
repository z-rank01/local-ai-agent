"""Lossless batches within provider pages; an empty page is not global coverage."""
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4


class PageSnapshots:
    """Container-local immutable transport snapshots, not a second evidence database."""
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, params, fingerprint):
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise ValueError('搜索页快照指纹无效')
        scope = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self.root / scope / (fingerprint + '.json')

    def save(self, params, fingerprint, data):
        path = self._path(params, fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        pending = path.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            pending.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)

    def read(self, params, fingerprint):
        try:
            return json.loads(self._path(params, fingerprint).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('搜索页快照不可用，请重新读取本页并按URL去重') from exc


def select_batch(data, *, page, offset, size, page_hash=None):
    if not isinstance(data, dict):
        raise ValueError('搜索服务未返回结果对象')
    rows = data.get('results')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('搜索服务返回的结果页结构无效')
    fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if offset and page_hash != fingerprint:
        raise ValueError('搜索页已变化或缺少页指纹，请重新读取本页并按URL去重')
    if offset > len(rows):
        raise ValueError('读取位置超过当前结果页')
    end = min(offset + size, len(rows))
    failed_engines = data.get('unresponsive_engines') or []
    status = ('PARTIAL' if rows else 'UNAVAILABLE') if failed_engines else ('OK' if rows else 'PAGE_EMPTY')
    following = ({'page': page, 'offset': end, 'page_hash': fingerprint} if end < len(rows)
                 else {'page': page + 1, 'offset': 0} if rows else None)
    return rows[offset:end], {'status': status, 'page': page, 'offset': offset,
        'page_hash': fingerprint, 'page_result_count': len(rows), 'next_cursor': following,
        'coverage': 'provider_page_only', 'provider_failures': failed_engines}
