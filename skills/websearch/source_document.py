"""Page-addressable public source snapshots. No model or workspace access."""
import base64
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO


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
