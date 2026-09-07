"""Guard against fetching search-engine result pages.

Search result pages are JS-driven; readability extraction on them yields
navigation/SEO residue, not article text, and each fetch burns the limited
web_fetch budget. Keep this module dependency-free so it stays unit-testable.
"""
import re

_SEARCH_PAGE_RE = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)*(?:"
    r"bing\.com/search|"
    r"google\.[a-z.]+/search|"
    r"baidu\.com/s(?:[/?#]|$)|"
    r"so\.com/s(?:[/?#]|$)|"
    r"sogou\.com/(?:web|sogou)|"
    r"(?:quark\.)?sm\.cn/s(?:[/?#]|$)|"
    r"toutiao\.com/search|"
    r"duckduckgo\.com/\?"
    r")",
    re.IGNORECASE,
)


def is_search_page(url: str) -> bool:
    """True when the URL is a search-engine result page rather than an article."""
    return bool(_SEARCH_PAGE_RE.search(url or ""))
