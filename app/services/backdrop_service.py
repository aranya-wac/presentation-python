"""Editor backdrop service.

Produces theme-tied background images for the editor canvas. Backdrops are
**editor-only** — exporters (PPTX/PDF/HTML) ignore the `editor_background`
field and continue to use the slide's `background` (color/gradient).

Strategy per (theme, variant):
1. Disk cache hit  → return immediately (zero cost).
2. Unsplash fetch  → photographic abstract on theme color (3s timeout).
3. PIL fallback    → numpy-vectorized radial gradient with accent flare.

Per-run in-process memoization dedupes parallel requests for the same
(theme, variant) pair so a 15-slide deck makes ≤5 distinct backdrop calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import random
from pathlib import Path
from typing import Optional

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Backdrops live under storage/imports/backdrops so they're served by the
# existing /imports/ static mount without adding new routes.
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # → backend/
_BACKDROPS_DIR = _BACKEND_DIR / "storage" / "imports" / "backdrops"
_URL_PREFIX = "/imports/backdrops"

# In-process memo: maps cache_key → asyncio.Task[str]. Lifetime = process
# (cleared only on restart). Disk cache is the durable layer; this just
# prevents parallel duplicate work inside one generation run.
_in_flight: dict[str, "asyncio.Task[str]"] = {}

# Variant → Unsplash search keywords. Tuned for Gamma-tier editorial quality:
# specific, cinematic, "luxury photography" vocabulary that pulls high-end
# stock instead of generic gradients. Hero/closing keywords are especially
# strong since (on free Gemini plans) AI generation is off and Unsplash
# carries the premium look alone.
_VARIANT_KEYWORDS = {
    "hero": [
        "luxury 3d render abstract", "premium silk fabric texture",
        "abstract sphere dark cinematic", "metallic abstract editorial",
        "dark luxury abstract render",
    ],
    "content": [
        "abstract cinematic dark texture", "luxury minimal dark surface",
        "editorial bokeh atmospheric", "premium abstract pattern dark",
    ],
    "quote": [
        "soft abstract atmospheric dark", "cinematic light bokeh editorial",
        "minimal abstract elegant",
    ],
    "stats": [
        "geometric editorial dark", "modern abstract cinematic shapes",
        "luxury abstract pattern minimal",
    ],
    "closing": [
        "cinematic abstract glow warm", "luxury sunset abstract render",
        "atmospheric warm gradient cinematic",
    ],
}

# AI image-gen prompts for hero/closing slides — Gamma uses bespoke imagery
# for these. {color_word} substituted at runtime from theme.primary.
_AI_PROMPTS = {
    "hero": (
        "Cinematic abstract {color_word} background, premium editorial photography, "
        "luxury dark mood, dramatic soft light flare, deep bokeh depth of field, "
        "16:9 widescreen, ultra-high quality, atmospheric, no text, no people, "
        "Gamma-style presentation backdrop, museum-quality fine art aesthetic."
    ),
    "closing": (
        "Cinematic warm {color_word} abstract glow, editorial luxury photography, "
        "dramatic light leak, atmospheric depth, soft bokeh, 16:9 widescreen, "
        "no text, no people, premium presentation backdrop, fine art quality."
    ),
}

# Map hex → human color word for AI prompts.
_COLOR_WORDS: list[tuple[tuple[int, int, int], str]] = [
    ((20, 20, 20),    "black"),
    ((240, 240, 240), "white"),
    ((200, 30, 30),   "crimson red"),
    ((130, 30, 200),  "deep purple"),
    ((30, 80, 220),   "midnight blue"),
    ((20, 160, 170),  "teal"),
    ((30, 180, 60),   "emerald green"),
    ((230, 130, 30),  "amber orange"),
    ((230, 200, 0),   "gold"),
    ((220, 30, 180),  "magenta"),
]

# Heavy overlays so the backdrop reads as atmospheric texture, NOT as the
# focal point. Gamma decks feel premium because LAYOUT dominates — the
# backdrop is just a hint of depth behind the page. 0.80–0.85 keeps just
# enough of the photo to break the flat-color feel without competing.
_VARIANT_OVERLAY = {
    "hero":    "rgba(0,0,0,0.30)",   # title/closing (unused — see _VARIANT_FOR_TYPE)
    "content": "rgba(0,0,0,0.82)",
    "quote":   "rgba(0,0,0,0.78)",
    "stats":   "rgba(0,0,0,0.85)",
    "closing": "rgba(0,0,0,0.30)",   # unused
}

# Unsplash color enum buckets (R, G, B) → enum name.
_UNSPLASH_COLORS: list[tuple[tuple[int, int, int], str]] = [
    ((0, 0, 0),       "black"),
    ((255, 255, 255), "white"),
    ((230, 200, 0),   "yellow"),
    ((230, 130, 30),  "orange"),
    ((200, 30, 30),   "red"),
    ((130, 30, 200),  "purple"),
    ((220, 30, 180),  "magenta"),
    ((30, 180, 60),   "green"),
    ((20, 160, 170),  "teal"),
    ((30, 80, 220),   "blue"),
]


# ── Public API ────────────────────────────────────────────────────────────────

def overlay_for(variant: str) -> str:
    return _VARIANT_OVERLAY.get(variant, _VARIANT_OVERLAY["content"])


async def get_backdrop(theme_colors: dict, variant: str) -> str:
    """Return a URL path (`/imports/backdrops/<file>.jpg`) for the backdrop.

    Idempotent + cached. Safe to call concurrently for many slides — same
    (theme, variant) collapses to one underlying fetch/generate.
    """
    if not settings.EDITOR_BACKDROPS_ENABLED:
        return ""
    if variant not in _VARIANT_KEYWORDS:
        variant = "content"

    _BACKDROPS_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(theme_colors, variant)
    cache_path = _BACKDROPS_DIR / f"{key}.jpg"
    url = f"{_URL_PREFIX}/{key}.jpg"

    # Fast path: file already on disk.
    if cache_path.exists():
        return url

    # Coalesce concurrent requests via in-process memo.
    if key in _in_flight:
        return await _in_flight[key]

    task = asyncio.create_task(_produce_backdrop(theme_colors, variant, cache_path, url))
    _in_flight[key] = task
    try:
        return await task
    finally:
        # Drop the task so future calls re-check the disk (in case the file
        # was evicted) but keep the disk cache itself.
        _in_flight.pop(key, None)


# ── Internals ─────────────────────────────────────────────────────────────────

def _cache_key(theme_colors: dict, variant: str) -> str:
    primary   = (theme_colors.get("primary")   or "#0F172A").lower()
    secondary = (theme_colors.get("secondary") or "#1E293B").lower()
    accent    = (theme_colors.get("accent")    or "#6366F1").lower()
    raw = f"{primary}|{secondary}|{accent}|{variant}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


async def _produce_backdrop(theme_colors: dict, variant: str, cache_path: Path, url: str) -> str:
    # Tier 1 (Gamma-quality): AI-generated bespoke imagery for hero/closing.
    # Slowest (~5-15s) but runs concurrently with LLM slide-content gen so
    # it doesn't add to wall-clock.
    if (
        settings.AI_HERO_BACKDROPS_ENABLED
        and settings.GEMINI_API_KEY
        and variant in _AI_PROMPTS
    ):
        try:
            ok = await asyncio.wait_for(
                _generate_ai_backdrop(theme_colors, variant, cache_path),
                timeout=20.0,
            )
            if ok:
                logger.info(f"AI backdrop generated for {variant}")
                return url
        except asyncio.TimeoutError:
            logger.info(f"AI backdrop timeout for {variant}; trying Unsplash")
        except Exception as exc:
            logger.warning(f"AI backdrop failed ({variant}): {exc}; trying Unsplash")

    # Tier 2: Unsplash search → best-of-results.
    if settings.UNSPLASH_ACCESS_KEY:
        try:
            ok = await asyncio.wait_for(
                _fetch_unsplash(theme_colors, variant, cache_path),
                timeout=4.0,
            )
            if ok:
                return url
        except asyncio.TimeoutError:
            logger.info(f"Unsplash timeout for {variant}; falling back to PIL")
        except Exception as exc:
            logger.warning(f"Unsplash fetch failed ({variant}): {exc}; falling back to PIL")

    # Tier 3: PIL fallback — vectorized radial gradient + vignette + grain.
    await asyncio.to_thread(_render_pil_backdrop, theme_colors, variant, cache_path)
    return url


async def _generate_ai_backdrop(theme_colors: dict, variant: str, cache_path: Path) -> bool:
    """Use Gemini Nano Banana to produce a bespoke cinematic backdrop.
    Returns True on success."""
    from app.ai import gemini_client

    prompt_tpl = _AI_PROMPTS[variant]
    color_word = _color_word(theme_colors.get("primary") or "#0F172A")
    prompt = prompt_tpl.format(color_word=color_word)

    img_bytes, _mime = await gemini_client.generate_image(prompt)
    if not img_bytes:
        return False

    # Re-encode to JPEG (Gemini returns PNG, JPEG is smaller for backdrops).
    from PIL import Image
    def _save() -> None:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # Resize to 1920×1080 if not already (Gemini often returns 1024×1024).
        if img.size != (1920, 1080):
            img = img.resize((1920, 1080), Image.LANCZOS)
        tmp_path = cache_path.with_suffix(".jpg.tmp")
        img.save(str(tmp_path), format="JPEG", quality=88, optimize=True)
        tmp_path.replace(cache_path)
    await asyncio.to_thread(_save)
    return True


async def _fetch_unsplash(theme_colors: dict, variant: str, cache_path: Path) -> bool:
    """Hit Unsplash search → pick the best-of-N result by likes count.
    `/photos/random` returns one image with no quality signal; `/search/photos`
    sorted by `relevant` lets us pick a high-engagement image deterministically."""
    import httpx

    keywords = _VARIANT_KEYWORDS.get(variant, _VARIANT_KEYWORDS["content"])
    keyword = random.choice(keywords)
    color = _nearest_unsplash_color(theme_colors.get("primary") or "#0F172A")

    params = {
        "query": keyword,
        "color": color,
        "orientation": "landscape",
        "content_filter": "high",
        "per_page": 12,
        "order_by": "relevant",
    }
    headers = {
        "Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}",
        "Accept-Version": "v1",
    }

    async with httpx.AsyncClient(timeout=3.5) as client:
        r = await client.get(
            "https://api.unsplash.com/search/photos",
            params=params, headers=headers,
        )
        if r.status_code != 200:
            logger.info(f"Unsplash returned {r.status_code} for {keyword}/{color}")
            return False
        data = r.json()
        results = data.get("results") or []
        if not results:
            return False
        # Pick the highest-liked candidate from the top results. `likes`
        # correlates strongly with visual quality on Unsplash.
        best = max(results, key=lambda p: p.get("likes", 0))
        # Prefer `full` (4k+) over `regular` (1080p) for higher fidelity.
        urls = best.get("urls") or {}
        img_url = urls.get("full") or urls.get("regular")
        if not img_url:
            return False
        img = await client.get(img_url)
        if img.status_code != 200:
            return False
        # Save atomically (write to tmp, then rename) so concurrent readers
        # never see a half-written JPEG.
        tmp_path = cache_path.with_suffix(".jpg.tmp")
        await asyncio.to_thread(tmp_path.write_bytes, img.content)
        await asyncio.to_thread(tmp_path.replace, cache_path)
        return True


def _render_pil_backdrop(theme_colors: dict, variant: str, cache_path: Path) -> None:
    """Synchronous (called via to_thread): generate a 1920×1080 radial-gradient
    JPEG using numpy for speed. Adds a soft accent-color flare and noise."""
    import numpy as np
    from PIL import Image

    W, H = 1920, 1080
    primary   = _hex_to_rgb(theme_colors.get("primary")   or "#0F172A")
    secondary = _hex_to_rgb(theme_colors.get("secondary") or "#1E293B")
    accent    = _hex_to_rgb(theme_colors.get("accent")    or "#6366F1")

    # Variant-specific flare position (px in 1920×1080 frame).
    flare_pos = {
        "hero":    (W * 0.72, H * 0.50, 0.55),  # right-center, large
        "content": (W * 0.85, H * 0.30, 0.40),  # upper-right, smaller
        "quote":   (W * 0.50, H * 0.50, 0.45),  # centered
        "stats":   (W * 0.20, H * 0.30, 0.40),  # upper-left
        "closing": (W * 0.50, H * 0.70, 0.55),  # lower-center
    }.get(variant, (W * 0.72, H * 0.50, 0.50))
    fx, fy, fsize = flare_pos

    # Radial distance field from frame center → secondary at edges, primary at middle.
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
    cx, cy = W * 0.5, H * 0.5
    d_center = np.sqrt(((xx - cx) / W) ** 2 + ((yy - cy) / H) ** 2)
    # Smooth ramp 0 → 1 (center → corner).
    t_center = np.clip(d_center * 1.6, 0.0, 1.0)
    t_center = t_center ** 1.4  # ease so middle stays rich

    # Base: primary → secondary blend.
    p = np.array(primary, dtype=np.float32)
    s = np.array(secondary, dtype=np.float32)
    base = p[None, None, :] * (1.0 - t_center)[..., None] + s[None, None, :] * t_center[..., None]

    # Accent flare: soft Gaussian-style blob centered at (fx, fy).
    d_flare = np.sqrt(((xx - fx) / W) ** 2 + ((yy - fy) / H) ** 2)
    flare_intensity = np.exp(-(d_flare / fsize) ** 2) * 0.55
    a = np.array(accent, dtype=np.float32)
    blended = base + (a[None, None, :] - base) * flare_intensity[..., None]

    # A second small white-hot specular highlight for "premium" sheen.
    sx, sy = fx + W * 0.04, fy - H * 0.08
    d_spec = np.sqrt(((xx - sx) / W) ** 2 + ((yy - sy) / H) ** 2)
    spec = np.exp(-(d_spec / 0.10) ** 2) * 0.25
    white = np.array((255, 255, 255), dtype=np.float32)
    blended = blended + (white[None, None, :] - blended) * spec[..., None]

    # A secondary off-axis bloom for organic light-leak feel — common in
    # premium editorial photography. Position varies per variant.
    bx, by = (W * 0.12, H * 0.85) if variant != "stats" else (W * 0.78, H * 0.85)
    d_bloom = np.sqrt(((xx - bx) / W) ** 2 + ((yy - by) / H) ** 2)
    bloom = np.exp(-(d_bloom / 0.45) ** 2) * 0.18
    blended = blended + (a[None, None, :] - blended) * bloom[..., None]

    # Vignette: darken corners for cinematic depth. Editorial photography
    # always has subtle vignetting.
    d_corner = np.sqrt(((xx - cx) / (W * 0.5)) ** 2 + ((yy - cy) / (H * 0.5)) ** 2)
    vignette = 1.0 - np.clip((d_corner - 0.7) * 0.35, 0.0, 0.35)
    blended = blended * vignette[..., None]

    # Subtle noise so it doesn't look CGI-clean. Film-grain texture.
    rng = np.random.default_rng(seed=int.from_bytes(cache_path.stem[:8].encode(), "little"))
    noise = (rng.standard_normal(size=(H, W, 1), dtype=np.float32) * 5.0)
    blended = blended + noise

    blended = np.clip(blended, 0, 255).astype(np.uint8)
    img = Image.fromarray(blended, mode="RGB")
    tmp_path = cache_path.with_suffix(".jpg.tmp")
    img.save(str(tmp_path), format="JPEG", quality=82, optimize=True)
    tmp_path.replace(cache_path)


# ── Color helpers ─────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (15, 23, 42)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (15, 23, 42)


def _nearest_unsplash_color(hex_color: str) -> str:
    """Pick the closest Unsplash `color` enum to the given hex."""
    r, g, b = _hex_to_rgb(hex_color)
    best_name = "black"
    best_d = math.inf
    for (cr, cg, cb), name in _UNSPLASH_COLORS:
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < best_d:
            best_d, best_name = d, name
    return best_name


def _color_word(hex_color: str) -> str:
    """Map a hex color → human color phrase for AI image prompts."""
    r, g, b = _hex_to_rgb(hex_color)
    best_name = "deep blue"
    best_d = math.inf
    for (cr, cg, cb), name in _COLOR_WORDS:
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < best_d:
            best_d, best_name = d, name
    return best_name
