from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import httpx
from lxml import html as lxml_html

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str  # publisher domain or "duckduckgo"


# Article-count caps per research depth — keeps Gemini token usage bounded
# and the SSE stream finite. Tune freely.
DEPTH_LIMITS = {"shallow": 5, "standard": 10, "deep": 15}


def _normalize_query(topic: str) -> str:
    return re.sub(r"\s+", " ", topic).strip()[:200]


async def _search_serper(topic: str, limit: int) -> list[SearchHit]:
    """Use Serper.dev (Google wrapper) when SERPER_API_KEY is configured.

    Returns recent articles from the News tab — much higher quality than the
    HTML scrape fallback for "current topic" workflows.
    """
    api_key = getattr(settings, "SERPER_API_KEY", "") or ""
    if not api_key:
        return []

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": topic, "num": limit, "tbs": "qdr:m"}  # past month

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://google.serper.dev/news",
            headers=headers,
            json=payload,
        )
        if resp.status_code >= 400:
            logger.warning(f"Serper returned {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()

    hits: list[SearchHit] = []
    for item in (data.get("news") or [])[:limit]:
        url = item.get("link") or item.get("url") or ""
        if not url:
            continue
        hits.append(SearchHit(
            title=(item.get("title") or "").strip(),
            url=url,
            snippet=(item.get("snippet") or "").strip(),
            source=(item.get("source") or "").strip() or _domain(url),
        ))
    return hits


_BING_REDIR_URL_RE = re.compile(r"[?&]url=([^&]+)")


async def _search_bing_news_rss(topic: str, limit: int) -> list[SearchHit]:
    """Free, no-key, reliable from cloud IPs: Bing News RSS.

    Bing's RSS is more permissive about datacenter IPs than DuckDuckGo, and
    crucially, every <link> embeds the real publisher URL in a query
    parameter so we don't need a JS browser to resolve a redirector.
    """
    from urllib.parse import unquote
    url = (
        "https://www.bing.com/news/search"
        f"?q={quote_plus(topic)}&format=rss"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Bing News RSS returned {resp.status_code}")
                return []
            body = resp.text
    except httpx.HTTPError as exc:
        logger.warning(f"Bing News RSS fetch failed: {exc}")
        return []

    # Use a regex pass over the raw RSS — lxml's HTML mode mangles RSS items
    # and the markup is small enough that regex is the simpler, sturdier choice.
    items = re.findall(r"<item>(.*?)</item>", body, re.DOTALL)
    hits: list[SearchHit] = []
    for item_xml in items:
        title_m = re.search(r"<title>(.*?)</title>", item_xml, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", item_xml, re.DOTALL)
        desc_m = re.search(r"<description>(.*?)</description>", item_xml, re.DOTALL)
        src_m = re.search(r"<News:Source>(.*?)</News:Source>", item_xml, re.DOTALL)
        if not title_m or not link_m:
            continue

        bing_redir = link_m.group(1).strip().replace("&amp;", "&")
        # Pull the real publisher URL out of the `url=` query param.
        url_m = _BING_REDIR_URL_RE.search(bing_redir)
        real_url = unquote(url_m.group(1)) if url_m else bing_redir
        if not real_url.startswith("http"):
            continue

        title = title_m.group(1).strip()
        snippet = (desc_m.group(1).strip() if desc_m else "")
        source = (src_m.group(1).strip() if src_m else _domain(real_url))
        hits.append(SearchHit(title=title, url=real_url, snippet=snippet[:300], source=source))
        if len(hits) >= limit:
            break
    return hits


async def _search_duckduckgo(topic: str, limit: int) -> list[SearchHit]:
    """Zero-config fallback: scrape the DuckDuckGo HTML SERP.

    Lower quality than Serper, but useful when SERPER_API_KEY is unset so the
    feature still demonstrably works. POST to /html/ is more reliable than
    GET (the GET path occasionally returns 202 + an anti-bot interstitial).
    """
    url = "https://html.duckduckgo.com/html/"
    # Browser-like headers reduce the chance of the anti-bot interstitial.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.post(url, content=f"q={quote_plus(topic + ' news')}")
            if resp.status_code != 200:
                # 202 = anti-bot interstitial, anything else = error.
                logger.warning(f"DuckDuckGo returned {resp.status_code} (likely anti-bot, set SERPER_API_KEY for reliable search)")
                return []
            body = resp.text
    except httpx.HTTPError as exc:
        logger.warning(f"DuckDuckGo fetch failed: {exc}")
        return []

    try:
        doc = lxml_html.fromstring(body)
    except Exception as exc:
        logger.warning(f"DuckDuckGo HTML parse failed: {exc}")
        return []

    hits: list[SearchHit] = []
    for result in doc.xpath("//div[contains(@class,'result')]")[: limit * 3]:
        title_els = result.xpath(".//a[contains(@class,'result__a')]")
        snippet_els = result.xpath(".//a[contains(@class,'result__snippet')]")
        if not title_els:
            continue
        a = title_els[0]
        href = a.get("href") or ""
        # DDG sometimes wraps results in /l/?uddg=…; extract real URL
        if "uddg=" in href:
            from urllib.parse import unquote, parse_qs, urlparse as _urlparse
            qs = parse_qs(_urlparse(href).query)
            if qs.get("uddg"):
                href = unquote(qs["uddg"][0])
        if not href.startswith("http"):
            continue
        title = (a.text_content() or "").strip()
        snippet = (snippet_els[0].text_content().strip() if snippet_els else "")
        hits.append(SearchHit(title=title, url=href, snippet=snippet, source=_domain(href)))
        if len(hits) >= limit:
            break
    return hits


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    """Drop near-duplicates by URL hostname+path and by title token-Jaccard."""
    seen_urls: set[str] = set()
    kept: list[SearchHit] = []
    for h in hits:
        from urllib.parse import urlparse
        try:
            p = urlparse(h.url)
            key = (p.netloc.lower(), p.path.rstrip("/"))
        except Exception:
            continue
        canon = f"{key[0]}{key[1]}"
        if canon in seen_urls:
            continue
        seen_urls.add(canon)

        if not _is_too_similar_to_any(h.title, kept):
            kept.append(h)
    return kept


def _tokenize(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", title.lower()) if len(t) > 3}


def _is_too_similar_to_any(title: str, existing: list[SearchHit]) -> bool:
    a = _tokenize(title)
    if len(a) < 3:
        return False
    for h in existing:
        b = _tokenize(h.title)
        if not b:
            continue
        intersection = len(a & b)
        union = len(a | b)
        if union and (intersection / union) > 0.6:
            return True
    return False


async def search(topic: str, depth: str = "standard") -> list[SearchHit]:
    """Search for recent news articles on `topic`.

    Tries Serper first (better recency + ranking), falls back to DuckDuckGo
    HTML scraping when no key is set. Results are deduped before return.
    """
    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["standard"])
    q = _normalize_query(topic)
    if not q:
        return []

    # Over-fetch slightly so dedupe has headroom.
    raw_limit = min(limit + 5, 25)

    hits = await _search_serper(q, raw_limit)
    if not hits:
        hits = await _search_bing_news_rss(q, raw_limit)
    if not hits:
        hits = await _search_duckduckgo(q, raw_limit)

    deduped = _dedupe(hits)
    return deduped[:limit]
