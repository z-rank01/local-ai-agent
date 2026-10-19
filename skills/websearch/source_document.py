"""Page-addressable public source snapshots. No model or workspace access."""
import base64
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlsplit


def json_body(text):
    """Read JSON or a JSONP wrapper as data, never execute JavaScript."""
    text = text.strip().lstrip('\ufeff')
    wrapper = re.fullmatch(r'[A-Za-z_$][\w.$]*\s*\((.*)\)\s*;?', text, re.S)
    try:
        value = json.loads(wrapper.group(1) if wrapper else text)
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


def json_document_links(text, url):
    """Expose PDF addresses actually present in a provider receipt."""
    found, seen = [], set()

    def visit(value, location):
        if len(found) == 20:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f'{location}.{key}')
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, f'{location}[{i}]')
        elif isinstance(value, str) and value.startswith(('https://', 'http://')):
            try:
                parts = urlsplit(value)
            except ValueError:
                return
            if (parts.scheme in ('http', 'https') and parts.netloc and
                    parts.path.lower().endswith('.pdf') and value not in seen):
                found.append({'url': value, 'label': location, 'found_on': url})
                seen.add(value)

    visit(json_body(text), '$')
    return found


def pdf_pages(raw: bytes) -> list[dict]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError('PDF 已加密，无法提取原文')
        pages = [{'page': i + 1, 'text': page.extract_text() or ''}
                 for i, page in enumerate(reader.pages)]
    except PdfReadError as exc:
        raise ValueError('PDF 为空或损坏，无法提取原文') from exc
    if not any(page['text'].strip() for page in pages):
        raise ValueError('PDF 没有可提取文本，可能需要 OCR；不能将空文本当成已读原文')
    return pages


def source_snapshot(raw, *, url, title, pages, offset=0, max_chars=20000,
                    expected_hash=None, include_file=False, media_type='text/html'):
    digest = sha256(raw).hexdigest()
    if expected_hash and expected_hash != digest:
        raise ValueError('来源已变化，请重新读取；不能拼接不同快照')
    text = '\n\n'.join(f"[第 {p['page']} 页]\n{p['text']}" for p in pages)
    if offset < 0 or offset > len(text):
        raise ValueError('offset 超出来源文本范围')
    end = min(len(text), offset + max_chars)
    result = dict(url=url, title=title, content=text[offset:end], char_count=end-offset,
                  total_chars=len(text), next_offset=end if end < len(text) else None,
                  sha256=digest, page_count=len(pages), media_type=media_type,
                  fetched_at=datetime.now(timezone.utc).isoformat())
    if include_file:
        result['_source_file'] = {'base64': base64.b64encode(raw).decode('ascii'), 'pages': pages}
    return result
