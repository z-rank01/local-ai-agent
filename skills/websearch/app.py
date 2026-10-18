"""
skill-websearch — Web search and page fetch service.

Provides two tools:
  - web_search: Search the web via SearXNG and return structured results
  - web_fetch:  Fetch a URL and extract readable text content
"""

import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from readability import Document

from search_pages import is_search_page
from source_document import pdf_pages, source_snapshot
from search_batches import select_batch, PageSnapshots, engine_capabilities
from pathlib import Path
import tempfile

logger = logging.getLogger("skill-websearch")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "30"))
MAX_FETCH_CHARS = int(os.environ.get("MAX_FETCH_CHARS", "20000"))
DEFAULT_SEARCH_RESULTS = 5
_page_snapshots = PageSnapshots(Path(tempfile.gettempdir()) / 'websearch-page-snapshots')

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

app = FastAPI(title="skill-websearch")
_client: httpx.AsyncClient | None = None
_engine_capabilities = None


@app.on_event("startup")
async def _startup():
    global _client, _engine_capabilities
    _client = httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers=_HEADERS,
        follow_redirects=True,
    )
    try:
        response = await _client.get(f"{SEARXNG_URL}/config", timeout=5)
        response.raise_for_status()
        _engine_capabilities = engine_capabilities(response.json())
    except (httpx.HTTPError, ValueError, TypeError):
        _engine_capabilities = None
        logger.warning("Search engine pagination capabilities unavailable")
    logger.info("skill-websearch started — searxng=%s", SEARXNG_URL)


@app.on_event("shutdown")
async def _shutdown():
    if _client:
        await _client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "skill-websearch"}


# ── web_search ──────────────────────────────────────────────────────────────

class WebSearchRequest(BaseModel):
    query: str
    max_results: Optional[int] = Field(default=DEFAULT_SEARCH_RESULTS, ge=1)
    language: Optional[str] = "zh-CN"
    time_range: Optional[str] = None  # day | week | month | year
    page: int = Field(default=1, ge=1, strict=True)
    offset: int = Field(default=0, ge=0, strict=True)
    page_hash: Optional[str] = Field(default=None, pattern=r'^[0-9a-f]{64}$')


@app.post("/tool/web_search")
async def web_search(req: WebSearchRequest):
    """Search the web via SearXNG and return structured results."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    max_results = min(req.max_results or DEFAULT_SEARCH_RESULTS, 20)

    params = {
        "q": req.query,
        "format": "json",
        "pageno": req.page,
    }
    language = (req.language or "").strip()
    if language:
        params["language"] = language
    if req.time_range:
        if req.time_range not in ("day", "week", "month", "year"):
            raise HTTPException(status_code=400, detail="time_range must be one of day/week/month/year")
        params["time_range"] = req.time_range

    logger.info("web_search: query=%r max_results=%d language=%r time_range=%r",
                req.query, max_results, language, req.time_range)

    try:
        if req.offset:
            data = _page_snapshots.read(params, req.page_hash)
        else:
            resp = await _client.get(f"{SEARXNG_URL}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data['_engine_capabilities'] = _engine_capabilities
    except ValueError as exc:
        return {"error": str(exc), "status": "PAGE_CHANGED_OR_INVALID", "results": [],
                "coverage": "unknown", "next_cursor": None}
    except httpx.TimeoutException:
        return {"error": "搜索超时，请稍后重试", "results": []}
    except Exception as exc:
        logger.error("SearXNG request failed: %s", exc)
        return {"error": f"搜索请求失败: {exc}", "results": []}

    try:
        raw_results, batch = select_batch(data, page=req.page, offset=req.offset,
                                         size=max_results, page_hash=req.page_hash)
        if not req.offset:
            _page_snapshots.save(params, batch['page_hash'], data)
    except ValueError as exc:
        return {"error": str(exc), "status": "PAGE_CHANGED_OR_INVALID", "results": [],
                "coverage": "unknown", "next_cursor": None}

    results = []
    for item in raw_results:
        snippet = _clean_text(item.get("content", ""))
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": snippet,
        })

    # Build a compact text summary for LLM consumption
    summary_parts = [f"搜索「{req.query}」第 {req.page} 页，本批 {len(results)} 条；不代表全部搜索范围："]
    if batch['non_paging_engines']:
        summary_parts.append('检索覆盖说明（非研究证据）：以下引擎不支持翻页：' +
                             '、'.join(batch['non_paging_engines']) + '。没有后续游标不代表没有更多资料。')
    elif batch['pagination_support'] == 'UNKNOWN':
        summary_parts.append('检索覆盖说明（非研究证据）：引擎分页能力未知。')
    for i, r in enumerate(results, 1):
        summary_parts.append(f"{i}. {r['title']} | {r['url']} | {r['snippet']}")

    return {
        **batch,
        "query": req.query,
        "total_results": len(results),
        "results": results,
        "summary": "\n".join(summary_parts),
    }


# ── web_fetch ───────────────────────────────────────────────────────────────

class WebFetchRequest(BaseModel):
    url: str
    max_chars: int = Field(default=MAX_FETCH_CHARS, ge=1, le=50000)
    offset: int = Field(default=0, ge=0)
    source_hash: str | None = None
    include_file: bool = False  # Internal router persistence; not a public tool parameter.


@app.post("/tool/web_fetch")
async def web_fetch(req: WebFetchRequest):
    """Fetch a URL and extract readable text content."""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url must not be empty")

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    if is_search_page(req.url):
        return {
            "error": "这是搜索引擎结果页，无法提取正文。请改用 web_search 搜索；web_fetch 只用于抓取搜索结果中的具体页面。",
            "url": req.url,
            "content": "",
        }

    max_chars = min(req.max_chars or MAX_FETCH_CHARS, 50000)

    logger.info("web_fetch: url=%r max_chars=%d", req.url, max_chars)

    try:
        async with _client.stream('GET', req.url) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 7 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail='来源超过7MiB，本次未提取或保存')
            resp = httpx.Response(response.status_code, headers=response.headers,
                                  content=bytes(raw), request=response.request)
    except httpx.TimeoutException:
        return {"error": "页面获取超时", "url": req.url, "content": ""}
    except httpx.HTTPError as exc:
        logger.error("web_fetch failed for %s: %s", req.url, exc)
        return {"error": f"页面获取失败: {exc}", "url": req.url, "content": ""}

    content_type = resp.headers.get("content-type", "")

    if 'application/pdf' in content_type or resp.content.startswith(b'%PDF-'):
        try:
            pages = pdf_pages(resp.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        content_type = 'application/pdf'
    elif "text/html" in content_type or "application/xhtml" in content_type:
        text = _extract_readable_text(resp.text, req.url)
        pages = [{'page': 1, 'text': text}]
    elif "text/" in content_type or "application/json" in content_type:
        text = resp.text
        pages = [{'page': 1, 'text': text}]
    else:
        return {
            "error": f"不支持的内容类型: {content_type}",
            "url": req.url,
            "content": "",
        }

    try:
        return source_snapshot(resp.content, url=str(resp.url),
            title=_extract_title(resp.text) if 'html' in content_type else '', pages=pages,
            offset=req.offset, max_chars=max_chars, expected_hash=req.source_hash,
            include_file=req.include_file, media_type=content_type)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ── helpers ─────────────────────────────────────────────────────────────────

def _extract_readable_text(html: str, url: str) -> str:
    """Use readability to extract main content, then strip tags."""
    try:
        doc = Document(html, url=url)
        content_html = doc.summary()
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text(separator="\n", strip=True)
    except Exception:
        # Fallback: basic tag stripping
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
        return soup.title.get_text(strip=True) if soup.title else ""
    except Exception:
        return ""


def _clean_text(text: str) -> str:
    """Remove HTML fragments from SearXNG snippets."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    return soup.get_text(strip=True)
