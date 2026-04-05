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
from pydantic import BaseModel
from readability import Document

logger = logging.getLogger("skill-websearch")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "30"))
MAX_FETCH_CHARS = int(os.environ.get("MAX_FETCH_CHARS", "20000"))
DEFAULT_SEARCH_RESULTS = 5

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

app = FastAPI(title="skill-websearch")
_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _client
    _client = httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers=_HEADERS,
        follow_redirects=True,
    )
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
    max_results: Optional[int] = DEFAULT_SEARCH_RESULTS


@app.post("/tool/web_search")
async def web_search(req: WebSearchRequest):
    """Search the web via SearXNG and return structured results."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    max_results = min(req.max_results or DEFAULT_SEARCH_RESULTS, 20)

    logger.info("web_search: query=%r max_results=%d", req.query, max_results)

    try:
        resp = await _client.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": req.query,
                "format": "json",
                "pageno": 1,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        return {"error": "搜索超时，请稍后重试", "results": []}
    except Exception as exc:
        logger.error("SearXNG request failed: %s", exc)
        return {"error": f"搜索请求失败: {exc}", "results": []}

    raw_results = data.get("results", [])[:max_results]

    results = []
    for item in raw_results:
        snippet = _clean_text(item.get("content", ""))
        if len(snippet) > 300:
            snippet = snippet[:297] + "..."
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": snippet,
        })

    # Build a compact text summary for LLM consumption
    summary_parts = [f"搜索「{req.query}」共 {len(results)} 条结果："]
    for i, r in enumerate(results, 1):
        summary_parts.append(f"{i}. {r['title']} | {r['url']} | {r['snippet']}")

    return {
        "query": req.query,
        "total_results": len(results),
        "results": results,
        "summary": "\n".join(summary_parts),
    }


# ── web_fetch ───────────────────────────────────────────────────────────────

class WebFetchRequest(BaseModel):
    url: str
    max_chars: Optional[int] = MAX_FETCH_CHARS


@app.post("/tool/web_fetch")
async def web_fetch(req: WebFetchRequest):
    """Fetch a URL and extract readable text content."""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url must not be empty")

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    max_chars = min(req.max_chars or MAX_FETCH_CHARS, 50000)

    logger.info("web_fetch: url=%r max_chars=%d", req.url, max_chars)

    try:
        resp = await _client.get(req.url)
        resp.raise_for_status()
    except httpx.TimeoutException:
        return {"error": "页面获取超时", "url": req.url, "content": ""}
    except Exception as exc:
        logger.error("web_fetch failed for %s: %s", req.url, exc)
        return {"error": f"页面获取失败: {exc}", "url": req.url, "content": ""}

    content_type = resp.headers.get("content-type", "")

    if "text/html" in content_type or "application/xhtml" in content_type:
        text = _extract_readable_text(resp.text, req.url)
    elif "text/" in content_type or "application/json" in content_type:
        text = resp.text
    else:
        return {
            "error": f"不支持的内容类型: {content_type}",
            "url": req.url,
            "content": "",
        }

    # Truncate to max_chars
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (内容已截断，共 {len(resp.text)} 字符)"

    return {
        "url": req.url,
        "title": _extract_title(resp.text) if "html" in content_type else "",
        "content": text,
        "char_count": len(text),
    }


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
