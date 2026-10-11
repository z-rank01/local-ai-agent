"""Lossless batches within provider pages; an empty page is not global coverage."""
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4


def engine_capabilities(config):
    """Public capability metadata only; configuration prose is never evidence."""
    if not isinstance(config, dict) or not isinstance(config.get('engines'), list):
        return None
    result = []
    for engine in config['engines']:
        if not isinstance(engine, dict) or engine.get('enabled') is not True:
            continue
        if not isinstance(engine.get('categories'), list):
            return None
        if 'general' not in engine['categories']:
            continue
        if (not isinstance(engine.get('name'), str) or
                not re.fullmatch(r'[A-Za-z0-9 _.-]{1,80}', engine['name']) or
                type(engine.get('paging')) is not bool or
                type(engine.get('time_range_support')) is not bool):
            return None  # Incomplete declarations must not imply full support.
        result.append({key: engine[key] for key in ('name', 'paging', 'time_range_support')})
    return result or None


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
    capabilities = data.get('_engine_capabilities')
    support = 'UNKNOWN'
    unsupported = []
    if (isinstance(capabilities, list) and capabilities and all(
            isinstance(engine, dict) and isinstance(engine.get('name'), str) and
            type(engine.get('paging')) is bool for engine in capabilities)):
        unsupported = [engine['name'] for engine in capabilities if engine['paging'] is False]
        support = ('UNSUPPORTED' if len(unsupported) == len(capabilities) else
                   'PARTIAL' if unsupported else 'SUPPORTED')
        # Reading the rest of this immutable page is always allowed.
        if support == 'UNSUPPORTED' and end == len(rows):
            following = None
    return rows[offset:end], {'status': status, 'page': page, 'offset': offset,
        'page_hash': fingerprint, 'page_result_count': len(rows), 'next_cursor': following,
        'coverage': 'provider_page_only', 'provider_failures': failed_engines,
        'pagination_support': support, 'non_paging_engines': unsupported,
        'diagnostic_role': 'UNTRUSTED_PROVIDER_STATUS_NOT_RESEARCH_EVIDENCE'}
