"""Lossless batches within provider pages; an empty page is not global coverage."""
import hashlib
import json


def select_batch(data, *, page, offset, size, page_hash=None):
    if not isinstance(data, dict):
        raise ValueError('搜索服务未返回结果对象')
    rows = data.get('results')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('搜索服务返回的结果页结构无效')
    fingerprint = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
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
