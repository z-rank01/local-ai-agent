"""Rebuildable paragraph index; originals and their versions remain authoritative."""
from contextlib import closing
import hashlib
import json
import re
import sqlite3
import math
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 2000
SCHEMA = '''CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS passages(path TEXT NOT NULL, location TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS by_path ON passages(path);'''


def query_terms(query):
    words, groups = [], []
    for token in re.findall(r'[\u3400-\u9fff]+|[^\W_]+', query.casefold()):
        if re.fullmatch(r'[\u3400-\u9fff]+', token):
            groups.extend(token[i:i + 2] for i in range(max(1, len(token) - 1)))
        else:
            words.append(token)
    return list(dict.fromkeys(words)), list(dict.fromkeys(groups))


def segments(path, raw):
    if path.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(stream=raw, filetype='pdf') as document:
            for number, page in enumerate(document, 1):
                text = page.get_text().strip()
                if text:
                    yield {'page': number}, text
    else:
        text = raw.decode('utf-8-sig')
        for number, paragraph in enumerate(re.split(r'\n\s*\n', text), 1):
            if paragraph.strip():
                yield {'paragraph': number}, paragraph.strip()


def fingerprint(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('文件超过 64 MiB 限制')
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('读取期间文件超过大小限制')
    return raw, hashlib.sha256(raw).hexdigest()


class Provider:
    def invoke(self, tool, arguments, context):
        directory = context.resolve_path(arguments.get('directory', ''))
        if not directory.is_dir():
            raise ValueError('资料目录不存在')
        cache = context.resolve_path(str(context.workspace_root / '.capabilities/document-index/index.sqlite'))
        if tool == 'documents_index':
            return self.update(directory, cache, context)
        if tool == 'documents_find':
            query = arguments.get('query')
            limit = arguments.get('limit', 5)
            if not isinstance(query, str) or not query.strip() or len(query) > 300:
                raise ValueError('关键词须为 1–300 字符')
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError('返回数量须为 1–20')
            return self.find(directory, cache, context, query.strip(), limit)
        raise ValueError('未登记的资料工具')

    def update(self, directory, cache, context):
        cache.parent.mkdir(parents=True, exist_ok=True)
        changed, unchanged, errors, seen = 0, 0, [], set()
        candidates = []
        # A bounded walk never traverses the host cache, hidden roots, or symlinks.
        import os
        for parent, folders, names in os.walk(directory, followlinks=False):
            folders[:] = [n for n in folders if not n.startswith('.') and not (Path(parent) / n).is_symlink()]
            for name in names:
                path = Path(parent) / name
                if path.suffix.lower() in {'.md', '.txt', '.pdf'} and not path.is_symlink():
                    candidates.append(path)
                    if len(candidates) > MAX_FILES:
                        raise ValueError('单次索引超过 2000 个文件，请缩小资料目录')
        with closing(sqlite3.connect(cache)) as db, db:
            db.executescript(SCHEMA)
            for source in sorted(candidates):
                try:
                    path = context.resolve_path(str(source))
                    seen.add(str(path))
                    raw, sha = fingerprint(path)
                    previous = db.execute('SELECT sha FROM files WHERE path=?', (str(path),)).fetchone()
                    if previous and previous[0] == sha:
                        unchanged += 1
                        continue
                    rows = list(segments(path, raw))
                    if not rows:
                        raise ValueError('未提取到文本；扫描件需先 OCR')
                    if fingerprint(path)[1] != sha:
                        raise ValueError('索引期间原文件变化，请重试')
                    db.execute('DELETE FROM passages WHERE path=?', (str(path),))
                    db.executemany('INSERT INTO passages VALUES(?,?,?)',
                        [(str(path), json.dumps(location), text) for location, text in rows])
                    db.execute('INSERT INTO files VALUES(?,?) ON CONFLICT(path) DO UPDATE SET sha=excluded.sha', (str(path), sha))
                    changed += 1
                except (OSError, ValueError, RuntimeError) as exc:
                    errors.append({'path': str(source), 'error': str(exc)})
            removed = 0
            for (path,) in db.execute('SELECT path FROM files').fetchall():
                if Path(path).is_relative_to(directory) and path not in seen:
                    db.execute('DELETE FROM passages WHERE path=?', (path,))
                    db.execute('DELETE FROM files WHERE path=?', (path,))
                    removed += 1
        return {'status': 'PARTIAL' if errors else 'COMPLETE', 'indexed': changed, 'unchanged': unchanged,
                'removed': removed, 'errors': errors, 'directory': str(directory), 'cache': str(cache)}

    def find(self, directory, cache, context, query, limit):
        if not cache.exists():
            return {'status': 'NOT_INDEXED', 'matches': [], 'message': '请先建立资料索引'}
        words, groups = query_terms(query)
        if not words and not groups:
            raise ValueError('关键词至少包含文字或数字')
        matches, stale, verified = [], set(), {}
        with closing(sqlite3.connect(cache.as_uri() + '?mode=ro', uri=True)) as db:
            for path, sha, location, text in db.execute('SELECT p.path,f.sha,p.location,p.text FROM passages p JOIN files f ON p.path=f.path'):
                folded = text.casefold()
                matched = [term for term in groups if term in folded]
                if (not Path(path).is_relative_to(directory) or not all(term in folded for term in words)
                        or groups and len(matched) < math.ceil(len(groups) / 2)):
                    continue
                if path not in verified:
                    try:
                        verified[path] = fingerprint(context.resolve_path(path))[1] == sha
                    except (OSError, ValueError):
                        verified[path] = False
                if not verified[path]:
                    stale.add(path)
                    continue
                terms = words + matched
                offset = min(folded.find(term) for term in terms)
                start = max(0, offset - 200)
                matches.append({'path': path, 'source_version': sha, **json.loads(location),
                                'text': text[start:start + 1600], 'excerpt_offset': start,
                                'truncated': start > 0 or len(text) > 1600,
                                'matched_terms': terms, 'match_score': len(terms) / (len(words) + len(groups))})
        matches.sort(key=lambda row: (-row['match_score'], row['path'], row.get('page', row.get('paragraph', 0))))
        return {'status': 'STALE' if stale else 'COMPLETE', 'matches': matches[:limit],
                'total_matches': len(matches), 'match_mode': 'words_and_chinese_bigrams',
                'stale_files': sorted(stale), 'limit': limit, 'query': query}
