from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException

from app.api.v1.generate_sync import _extract_url_text  # SSRF-hardened
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Article:
    url: str
    title: str
    text: str
    source: str
    snippet: str = ""

    @property
    def truncated(self) -> str:
        # Per-article cap so a single huge article can't drown the prompt.
        # 8 KB ≈ ~1.5k tokens; with 10 articles we land around 15k tokens
        # of source material — comfortable for Gemini 2.5-flash's window.
        if len(self.text) > 8000:
            return self.text[:8000] + "\n…[truncated]"
        return self.text


async def fetch_one(url: str, fallback_title: str = "", source: str = "", snippet: str = "") -> Article | None:
    """Fetch one article. Returns None on any failure (logged) so a single
    bad URL doesn't poison the whole pipeline."""
    try:
        title, text = await _extract_url_text(url)
    except HTTPException as exc:
        logger.info(f"Article extract skipped {url}: {exc.detail}")
        return None
    except Exception as exc:
        logger.warning(f"Article extract error {url}: {exc}")
        return None

    if not text or len(text.split()) < 80:
        # Too thin to be useful — likely a paywall stub or 404 page.
        return None

    return Article(
        url=url,
        title=(title or fallback_title or url)[:300],
        text=text,
        source=source,
        snippet=snippet,
    )


async def fetch_many(
    items: list[tuple[str, str, str, str]],
    max_concurrency: int = 4,
) -> list[Article]:
    """Fetch many articles concurrently.

    `items` is a list of (url, fallback_title, source, snippet). Returns the
    successfully extracted ones in original order — failures are dropped.
    """
    sem = asyncio.Semaphore(max_concurrency)

    async def _bounded(idx: int, url: str, title: str, source: str, snippet: str):
        async with sem:
            article = await fetch_one(url, title, source, snippet)
            return idx, article

    tasks = [
        _bounded(i, url, title, source, snippet)
        for i, (url, title, source, snippet) in enumerate(items)
    ]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda r: r[0])
    return [a for _, a in results if a is not None]
