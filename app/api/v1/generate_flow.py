"""
Flow generation endpoint — `POST /api/v1/generate/flow`.

Produces a Gamma-style deck in the flow `Card[]` model: the AI emits card
*intents*, the `flow_composer` turns them into FlowBlock trees. The deck shares
one atmospheric **background image coloured to the theme**, and (at the advanced
level) ~half the cards get a **content illustration** generated from that card's
own subject. Additive: the legacy generation paths are untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.prompt_templates import (
    FLOW_CARD_EDIT_PROMPT,
    FLOW_CARD_PROMPT,
    FLOW_GENERATION_PROMPT,
    render,
)
from app.api.dependencies import get_current_user
from app.core.cache import cache_get_json, cache_set_json
from app.core.database import get_db
from app.models.presentation import Presentation
from app.models.template import Template
from app.models.theme import Theme
from app.models.user import User
from app.services.flow_composer import compose_card, compose_deck
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/generate", tags=["generation"])


class FlowGenerateRequest(BaseModel):
    prompt: str
    slide_count: int = 10
    level: str = "advanced"
    theme_id: str | None = None
    template_id: str | None = None


class FlowCardRequest(BaseModel):
    prompt: str
    deck_title: str = ""
    card_index: int = 0
    total_cards: int = 1
    level: str = "advanced"


class FlowBackdropRequest(BaseModel):
    theme: dict


class FlowCardChatRequest(BaseModel):
    card: dict
    message: str


class FlowThemeBackdropsRequest(BaseModel):
    theme: dict
    force: bool = False


class FlowIllustrationRequest(BaseModel):
    subject: str
    accent: str = ""


# Fallback theme — a dark "studio" palette matching the app's existing deck
# look, used when no theme_id is given or the requested theme can't be found.
_DEFAULT_THEME: dict = {
    "id": "flow-dark",
    "name": "Studio Dark",
    "colors": {
        "primary": "#ffffff",
        "secondary": "#9aa6b8",
        "accent": "#6ea8ff",
        "background": "#10131c",
        "text": "#dfe5ee",
    },
    "fonts": {
        "heading": {"family": "Inter, sans-serif", "size": 40, "weight": 800},
        "body": {"family": "Inter, sans-serif", "size": 18, "weight": 400},
        "caption": {"family": "Inter, sans-serif", "size": 13, "weight": 500},
    },
}


def _backdrop_prompt(colors: dict) -> str:
    """A theme-coloured atmospheric backdrop prompt, shared by every card."""
    bg = colors.get("background", "#10131c")
    accent = colors.get("accent", "#6ea8ff")
    return (
        "Premium abstract atmospheric photograph for a presentation background. "
        "Subject: sculptural layered organic surfaces — curved dunes, smooth "
        "architectural wave-folds, or stone-like topographic ridges — with real "
        "depth and three-dimensional form. "
        f"Colour mood built around {bg}: deep, rich, dark tones in that colour "
        f"family, NOT flat — dramatic soft grazing light rakes across the "
        f"surface revealing fine texture, with subtle luminous highlights tinted "
        f"toward {accent} along the crests and ridges. "
        "Refined, editorial, magazine-cover quality. Keep the lower and central "
        "areas darker so white text overlays cleanly, while the image stays "
        "textured and dimensional throughout. NO people, NO text, NO words, "
        "NO logos, NO charts, NO icons. 16:9 landscape, high detail. The "
        "texture MUST fill the entire frame corner to corner — absolutely NO "
        "white borders, NO margins, NO frame, NO side panels, NO letterboxing, "
        "NO solid colour bands along any edge."
    )


def _content_image_prompt(subject: str) -> str:
    """Wrap the AI's per-card subject in a consistent illustration style."""
    return (
        f"Editorial illustration for a dark-themed presentation slide. "
        f"Subject: {subject}. "
        f"Style: clean modern minimal line illustration with luminous soft "
        f"strokes — an isolated subject, no scenery, no frame, no border. "
        f"Render it on a SOLID, completely dark near-black background that fills "
        f"the entire image edge to edge, so it blends into a dark slide. "
        f"Absolutely NO white background, NO checkerboard pattern, NO "
        f"transparency-grid pattern, NO grey squares, NO card or panel behind "
        f"the subject. NO banner bars, NO horizontal bars, NO rectangles, NO "
        f"frame, NO UI mockup, NO slide layout — ONLY the single isolated "
        f"subject, centered. NO text, NO words, NO labels, NO captions, NO charts."
    )


def _theme_to_dict(theme: Theme) -> dict:
    return {
        "id": str(theme.id),
        "name": theme.name,
        "colors": theme.colors or _DEFAULT_THEME["colors"],
        "fonts": theme.fonts or _DEFAULT_THEME["fonts"],
    }


async def _template_backdrop_url(
    db: AsyncSession, template_id: str, request: Request
) -> str | None:
    """The chosen template's own backdrop image, taken from its preview deck."""
    prev = (
        await db.execute(
            select(Presentation).where(
                Presentation.template_id == template_id,
                Presentation.is_preview == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if not prev or not prev.slides:
        return None
    for slide in prev.slides:
        if not isinstance(slide, dict):
            continue
        for b in slide.get("blocks", []) or []:
            if not isinstance(b, dict) or b.get("type") != "image":
                continue
            bid = str(b.get("id", ""))
            url = b.get("content")
            if url and (bid.startswith("deck-bg") or bid.startswith("preview-illust")):
                if str(url).startswith("http"):
                    return url
                return f"{str(request.base_url).rstrip('/')}/{str(url).lstrip('/')}"
    return None


def _cutout_bg(img_bytes: bytes) -> bytes:
    """Key the flat background out to true transparency with a luminance mask.

    The illustration *should* be luminous strokes on a near-black background —
    but the image model sometimes returns the opposite (a light/white
    background, occasionally with stray banner bars). So we sample the four
    corners to detect which it is, then key out whichever background is
    actually there:

    - dark background  -> key out the dark, keep the bright strokes
    - light background -> key out the light, keep the dark linework

    Either way the flat background (and any solid white bars) become
    transparent, with a luminance ramp for soft anti-aliased edges. PNG bytes.
    """
    from io import BytesIO
    from PIL import Image, ImageChops

    im = Image.open(BytesIO(img_bytes)).convert("RGBA")
    lum = im.convert("L")
    w, h = lum.size
    # Average luminance of the four corners ≈ the background.
    corners = [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)]
    bg_lum = sum(lum.getpixel(p) for p in corners) / 4

    if bg_lum < 128:
        # Dark background: <=lo fully transparent, >=hi fully opaque.
        lo, hi = 22, 60
        lut = [0 if v <= lo else (255 if v >= hi else int(255 * (v - lo) / (hi - lo))) for v in range(256)]
    else:
        # Light background: >=hi fully transparent, <=lo fully opaque.
        lo, hi = 188, 232
        lut = [255 if v <= lo else (0 if v >= hi else int(255 * (hi - v) / (hi - lo))) for v in range(256)]

    mask = lum.point(lut)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _trim_uniform_border(img_bytes: bytes, ext: str) -> tuple[bytes, str]:
    """Crop solid flat-colour borders the image model sometimes bakes in — white
    side panels, a frame, letterboxing. We take the top-left corner as the
    candidate border colour and crop to the bounding box of everything that
    differs from it. Returns the image unchanged when there is no real border.
    """
    from io import BytesIO
    from PIL import Image, ImageChops

    im = Image.open(BytesIO(img_bytes))
    rgb = im.convert("RGB")
    w, h = rgb.size
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    # Pixels within a small tolerance of the corner colour count as border.
    diff = ImageChops.difference(rgb, bg).convert("L").point(
        lambda v: 255 if v > 14 else 0
    )
    bbox = diff.getbbox()
    if not bbox:
        return img_bytes, ext  # whole image is one flat colour — leave it
    left, top, right, bottom = bbox
    # No meaningful border (artwork already reaches every edge) — leave it.
    if left < 8 and top < 8 and right > w - 8 and bottom > h - 8:
        return img_bytes, ext
    cropped = im.crop(bbox).convert("RGB")
    out = BytesIO()
    cropped.save(out, format="JPEG", quality=88)
    return out.getvalue(), "jpg"


async def _save_image(prompt: str, request: Request, cutout: bool = False) -> str | None:
    """Generate one image, persist it, return an absolute served URL.

    The model occasionally bakes a flat border into the image (white side
    panels / a frame) — `_trim_uniform_border` crops that off so the artwork is
    genuinely full-bleed. When `cutout` is set, the background is then keyed out
    to real transparency so the illustration floats cleanly on the slide.
    """
    from app.api.v1.images import GENERATED_DIR

    try:
        img_bytes, mime = await asyncio.wait_for(
            gemini_client.generate_image(prompt), timeout=45.0
        )
        ext = mime.split("/")[-1] if "/" in mime else "png"
        # Crop any flat border the model baked in.
        try:
            img_bytes, ext = _trim_uniform_border(img_bytes, ext)
        except Exception as exc:  # noqa: BLE001 — non-fatal, keep the raw image
            logger.warning(f"flow border-trim failed: {exc}")
        if cutout:
            try:
                img_bytes = _cutout_bg(img_bytes)
                ext = "png"
            except Exception as exc:  # noqa: BLE001 — fall back to the raw image
                logger.warning(f"flow cutout failed, using raw image: {exc}")
        if ext == "jpeg":
            ext = "jpg"
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4()}.{ext}"
        (GENERATED_DIR / fname).write_bytes(img_bytes)
        return f"{str(request.base_url).rstrip('/')}/generated/{fname}"
    except Exception as exc:  # noqa: BLE001 — image generation is non-fatal
        logger.warning(f"flow image generation failed: {exc}")
        return None


async def _gen_card_images(cards: list, request: Request, cap: int) -> dict[int, str]:
    """Generate content illustrations for the cards that asked for one (capped)."""
    targets: list[tuple[int, str]] = []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        subject = (card.get("imagePrompt") or "").strip()
        if card.get("wantsImage") and subject:
            targets.append((i, subject))
        if len(targets) >= cap:
            break
    if not targets:
        return {}
    results = await asyncio.gather(
        *[_save_image(_content_image_prompt(subj), request, cutout=True) for (_, subj) in targets]
    )
    return {idx: url for (idx, _), url in zip(targets, results) if url}


@router.post("/flow")
async def generate_flow(
    req: FlowGenerateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a Gamma-style flow deck (content + charts + tables + images)."""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    slide_count = max(3, min(20, req.slide_count or 10))
    half_count = max(1, slide_count // 2)

    flow_prompt = render(
        FLOW_GENERATION_PROMPT,
        prompt=prompt,
        content=prompt,
        slide_count=slide_count,
        level=req.level or "advanced",
    )

    # Resolve the theme first — the backdrop is coloured to match it.
    # Priority: explicit theme_id → the chosen template's theme → default.
    theme_dict = dict(_DEFAULT_THEME)
    resolved_theme: Theme | None = None
    if req.theme_id:
        resolved_theme = (
            await db.execute(select(Theme).where(Theme.id == req.theme_id))
        ).scalar_one_or_none()
    if resolved_theme is None and req.template_id:
        tpl = (
            await db.execute(select(Template).where(Template.id == req.template_id))
        ).scalar_one_or_none()
        if tpl and tpl.theme_id:
            resolved_theme = (
                await db.execute(select(Theme).where(Theme.id == tpl.theme_id))
            ).scalar_one_or_none()
    if resolved_theme is not None:
        theme_dict = _theme_to_dict(resolved_theme)

    # Backdrop: a chosen template reuses ITS OWN background image; otherwise a
    # fresh atmospheric one is generated, coloured to the theme.
    template_backdrop = (
        await _template_backdrop_url(db, req.template_id, request) if req.template_id else None
    )
    try:
        if template_backdrop:
            generation = await gemini_client.generate_json(flow_prompt)
            background_url: str | None = template_backdrop
        else:
            generation, background_url = await asyncio.gather(
                gemini_client.generate_json(flow_prompt),
                _save_image(_backdrop_prompt(theme_dict["colors"]), request),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Flow generation failed: {exc}") from exc
    token_count = gemini_client.get_last_token_count()

    if not isinstance(generation, dict) or not generation.get("cards"):
        raise HTTPException(status_code=502, detail="Generator returned no cards")

    # Per-card content illustrations — only at the advanced level. Simple is
    # text-focused: no generated content images.
    if (req.level or "").lower() == "advanced":
        image_urls = await _gen_card_images(generation.get("cards", []), request, half_count)
    else:
        image_urls = {}

    deck = compose_deck(generation, background_url, image_urls)

    return {
        "title": generation.get("title", prompt[:60]),
        "cards": deck,
        "theme": theme_dict,
        "token_count": token_count,
    }


@router.post("/flow/backdrop")
async def generate_flow_backdrop(
    req: FlowBackdropRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Regenerate the shared atmospheric backdrop image for a deck's theme."""
    colors = (req.theme or {}).get("colors") or _DEFAULT_THEME["colors"]
    url = await _save_image(_backdrop_prompt(colors), request)
    if not url:
        raise HTTPException(status_code=502, detail="Backdrop generation failed")
    return {"url": url}


@router.post("/flow/illustration")
async def generate_flow_illustration(
    req: FlowIllustrationRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Regenerate a single content illustration through the SAME pipeline the
    full deck generation uses: a luminous line illustration on a solid dark
    background, then luminance-keyed to true transparency. This guarantees a
    clean cut-out illustration — never a baked-in checkerboard / grey-square
    'transparency' pattern."""
    subject = (req.subject or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="Subject cannot be empty")
    prompt = _content_image_prompt(subject)
    if req.accent:
        prompt += f" Use {req.accent} as the dominant luminous stroke colour."
    url = await _save_image(prompt, request, cutout=True)
    if not url:
        raise HTTPException(status_code=502, detail="Illustration generation failed")
    return {"url": url}


@router.post("/flow/card")
async def generate_flow_card(
    req: FlowCardRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Regenerate a single flow card. Colours are theme-driven at render time,
    so the card is composed with the default theme and no backdrop — the editor
    keeps the existing card's background when it swaps the result in."""
    topic = (req.prompt or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    total = max(1, req.total_cards)
    index = max(0, min(req.card_index, total - 1))
    card_prompt = render(
        FLOW_CARD_PROMPT,
        prompt=topic,
        deck_title=req.deck_title or topic,
        card_number=index + 1,
        total_cards=total,
        level=req.level or "advanced",
    )
    try:
        intent = await gemini_client.generate_json(card_prompt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Card generation failed: {exc}") from exc
    if not isinstance(intent, dict) or not intent.get("title"):
        raise HTTPException(status_code=502, detail="Generator returned no card")

    card = compose_card(intent, index, total)
    return {"card": card, "token_count": gemini_client.get_last_token_count()}


@router.post("/flow/card-chat")
async def flow_card_chat(
    req: FlowCardChatRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """AI-edit a single flow card from a natural-language instruction."""
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not isinstance(req.card, dict) or not req.card.get("root"):
        raise HTTPException(status_code=400, detail="A valid card is required")

    prompt = render(
        FLOW_CARD_EDIT_PROMPT,
        card_json=json.dumps(req.card, ensure_ascii=False),
        message=message,
    )
    try:
        result = await gemini_client.generate_json(prompt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Card edit failed: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("card"), dict):
        raise HTTPException(status_code=502, detail="Editor returned no card")

    return {
        "card": result["card"],
        "reply": result.get("reply", "Updated the card."),
        "token_count": gemini_client.get_last_token_count(),
    }


@router.post("/flow/theme-backdrops")
async def generate_flow_theme_backdrops(
    req: FlowThemeBackdropsRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Two atmospheric backdrop images for a theme — a hero backdrop (the first
    and last cards) and a content backdrop (every card in between). Cached per
    theme palette so switching back to a theme is instant; `force` regenerates.

    The cache is best-effort: if Redis is unavailable the images are simply
    regenerated each time rather than failing the request."""
    colors = (req.theme or {}).get("colors") or _DEFAULT_THEME["colors"]
    seed = json.dumps(colors, sort_keys=True)
    cache_key = "flowbackdrops:v1:" + hashlib.md5(seed.encode()).hexdigest()

    if not req.force:
        try:
            cached = await cache_get_json(cache_key)
        except Exception as exc:  # noqa: BLE001 — cache is best-effort
            logger.warning(f"backdrop cache read failed: {exc}")
            cached = None
        if isinstance(cached, dict) and cached.get("hero_url") and cached.get("content_url"):
            return {**cached, "cached": True}

    hero_url, content_url = await asyncio.gather(
        _save_image(_backdrop_prompt(colors), request),
        _save_image(_backdrop_prompt(colors), request),
    )
    if not hero_url or not content_url:
        raise HTTPException(status_code=502, detail="Backdrop generation failed")

    result = {"hero_url": hero_url, "content_url": content_url}
    try:
        await cache_set_json(cache_key, result, ttl=2592000)
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        logger.warning(f"backdrop cache write failed: {exc}")
    return {**result, "cached": False}
