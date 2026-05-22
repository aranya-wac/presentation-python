"""Stock photo search service.

Wraps the Unsplash `/search/photos` endpoint for the editor's image picker.
The editor-backdrop service (`backdrop_service.py`) also hits Unsplash, but
that path is keyword-tuned for atmospheric backgrounds; this one runs a
free-text user query and returns a flat list of thumbnail + full URLs the
frontend can render in a grid.

When `UNSPLASH_ACCESS_KEY` is unset the search returns an empty list rather
than erroring — the picker simply shows "no results".
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


@dataclass
class StockPhoto:
    id: str
    thumb_url: str   # small, for the results grid
    full_url: str    # high-res, used as the picked image
    alt: str         # description / alt text
    author: str      # photographer name (attribution)


async def search_photos(query: str, per_page: int = 24) -> list[StockPhoto]:
    """Search Unsplash for photos matching `query`.

    Returns up to `per_page` results ordered by relevance. Empty list if no
    Unsplash key is configured or the search yields nothing.
    """
    query = (query or "").strip()
    if not query:
        return []
    if not settings.UNSPLASH_ACCESS_KEY:
        logger.info("Stock photo search skipped — UNSPLASH_ACCESS_KEY not set")
        return []

    per_page = max(1, min(per_page, 30))
    params = {
        "query": query,
        "orientation": "landscape",
        "content_filter": "high",
        "per_page": per_page,
        "order_by": "relevant",
    }
    headers = {
        "Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}",
        "Accept-Version": "v1",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(_UNSPLASH_SEARCH_URL, params=params, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                f"Unsplash search returned {resp.status_code} for '{query}': "
                f"{resp.text[:200]}"
            )
            return []
        data = resp.json()

    photos: list[StockPhoto] = []
    for item in data.get("results") or []:
        urls = item.get("urls") or {}
        thumb = urls.get("small") or urls.get("thumb")
        full = urls.get("regular") or urls.get("full") or urls.get("raw")
        if not thumb or not full:
            continue
        user = item.get("user") or {}
        photos.append(
            StockPhoto(
                id=str(item.get("id") or ""),
                thumb_url=thumb,
                full_url=full,
                alt=(item.get("alt_description") or item.get("description") or query),
                author=str(user.get("name") or ""),
            )
        )
    return photos
