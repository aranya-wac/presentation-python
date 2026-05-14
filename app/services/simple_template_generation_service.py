"""Generation flow for `slide_source='simple'` templates.

How it differs from rich-template generation:
- Rich templates carry block-level layout in JSON; AI rewrites the text only.
- Simple templates carry just `{title, layout_type, prompt_hint}` per slide.
  We pass all of those + the user's prompt to Gemini in a single call,
  Gemini returns structured content per layout, and we render it with a
  small layout dictionary into the same Slide JSON shape used everywhere
  else (so the existing renderer/exporter just works).
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini_client import generate_json, get_last_token_count
from app.core.exceptions import GeminiError, NotFoundError, ValidationError
from app.models.presentation import Presentation
from app.models.role_prompt import RolePromptProfile
from app.models.template import Template
from app.models.template_slide import TemplateSlide
from app.models.theme import Theme
from app.models.user import User
from app.schemas.presentation import PresentationDetail
from app.schemas.template import GenerateFromSimpleTemplateRequest
from app.services import presentation_service
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Layout dictionary ───────────────────────────────────────────────────────
# Each layout maps a Gemini-returned content dict → a list of block dicts.
# Positions assume the standard 1280×720 canvas. No styling engine — we use
# theme-friendly defaults so the output looks reasonable on any theme.

_FONT_FAMILY = "Inter"


def _styling(
    *,
    size: int,
    weight: int = 600,
    color: str = "#1A1A2E",
    align: str = "left",
    bg: str = "transparent",
) -> dict:
    return {
        "font_family": _FONT_FAMILY,
        "font_size": size,
        "font_weight": weight,
        "color": color,
        "background_color": bg,
        "text_align": align,
    }


def _position(x: float, y: float, w: float, h: float) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def _block(block_id: str, btype: str, content: str, position: dict, styling: dict) -> dict:
    return {
        "id": block_id,
        "type": btype,
        "content": content,
        "position": position,
        "styling": styling,
    }


def _render_title(slide_id: str, content: dict) -> list[dict]:
    heading = content.get("heading", "") or ""
    subtitle = content.get("subtitle", "") or ""
    return [
        _block(
            f"{slide_id}-title", "title", heading,
            _position(80, 280, 1120, 140),
            _styling(size=68, weight=800, align="center"),
        ),
        _block(
            f"{slide_id}-subtitle", "subtitle", subtitle,
            _position(80, 440, 1120, 80),
            _styling(size=28, weight=400, color="#475569", align="center"),
        ),
    ]


def _render_bullets(slide_id: str, content: dict) -> list[dict]:
    heading = content.get("heading", "") or ""
    bullets = content.get("bullets", []) or []
    if not isinstance(bullets, list):
        bullets = []
    blocks = [
        _block(
            f"{slide_id}-heading", "heading", heading,
            _position(80, 80, 1120, 90),
            _styling(size=44, weight=700),
        ),
    ]
    bullet_text = "\n".join(f"• {str(b).strip()}" for b in bullets if str(b).strip())
    if bullet_text:
        blocks.append(
            _block(
                f"{slide_id}-bullets", "bullets", bullet_text,
                _position(80, 200, 1120, 480),
                _styling(size=24, weight=400, color="#1A1A2E"),
            )
        )
    return blocks


def _render_image(slide_id: str, content: dict) -> list[dict]:
    heading = content.get("heading", "") or ""
    caption = content.get("caption", "") or ""
    image_url = content.get("image_url", "") or ""
    blocks = [
        _block(
            f"{slide_id}-heading", "heading", heading,
            _position(80, 80, 1120, 90),
            _styling(size=44, weight=700),
        ),
        _block(
            f"{slide_id}-image", "image", image_url,
            _position(80, 200, 540, 400),
            _styling(size=14),
        ),
        _block(
            f"{slide_id}-caption", "caption", caption,
            _position(660, 220, 540, 380),
            _styling(size=22, weight=400, color="#475569"),
        ),
    ]
    return blocks


def _render_columns(slide_id: str, content: dict) -> list[dict]:
    heading = content.get("heading", "") or ""
    columns = content.get("columns", []) or []
    if not isinstance(columns, list):
        columns = []
    columns = columns[:3]  # cap at 3 for layout sanity
    blocks = [
        _block(
            f"{slide_id}-heading", "heading", heading,
            _position(80, 80, 1120, 90),
            _styling(size=44, weight=700),
        ),
    ]
    if not columns:
        return blocks
    n = len(columns)
    gap = 32
    total_w = 1120
    col_w = (total_w - gap * (n - 1)) / n
    for i, col in enumerate(columns):
        if not isinstance(col, dict):
            continue
        col_heading = col.get("heading", "") or ""
        col_body = col.get("body", "") or ""
        x = 80 + i * (col_w + gap)
        blocks.append(
            _block(
                f"{slide_id}-col{i}-h", "heading", col_heading,
                _position(x, 220, col_w, 60),
                _styling(size=22, weight=700, color="#0F3460"),
            )
        )
        blocks.append(
            _block(
                f"{slide_id}-col{i}-b", "text", col_body,
                _position(x, 290, col_w, 360),
                _styling(size=18, weight=400, color="#1A1A2E"),
            )
        )
    return blocks


_LAYOUT_RENDERERS = {
    "title": _render_title,
    "bullets": _render_bullets,
    "image": _render_image,
    "columns": _render_columns,
}


def _layout_output_shape(layout_type: str) -> str:
    """Human-readable description of the JSON shape Gemini should return per layout.
    Embedded in the prompt so the model knows exactly what keys to emit."""
    if layout_type == "title":
        return '{"heading": str, "subtitle": str}'
    if layout_type == "bullets":
        return '{"heading": str, "bullets": [str, str, ...]}'
    if layout_type == "image":
        return '{"heading": str, "caption": str, "image_url": str (may be empty)}'
    if layout_type == "columns":
        return (
            '{"heading": str, "columns": ['
            '{"heading": str, "body": str}, '
            '{"heading": str, "body": str}, '
            '...up to 3]}'
        )
    return "{}"


# ── Generation entrypoint ───────────────────────────────────────────────────

async def generate_from_simple_template(
    db: AsyncSession,
    user: User,
    template_id: str,
    req: GenerateFromSimpleTemplateRequest,
) -> PresentationDetail:
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise NotFoundError(f"Template {template_id} not found")

    # Visibility check — same rules as detail endpoint
    visible = (
        template.is_system
        or template.is_published
        or str(template.created_by) == str(user.id)
        or getattr(user, "role", None) == "admin"
    )
    if not visible:
        raise NotFoundError(f"Template {template_id} not found")

    if template.slide_source != "simple":
        raise ValidationError(
            "This endpoint only generates from 'simple' (wizard-built) templates."
        )

    rows = (
        await db.execute(
            select(TemplateSlide)
            .where(TemplateSlide.template_id == template.id)
            .order_by(TemplateSlide.order)
        )
    ).scalars().all()
    if not rows:
        raise ValidationError("Template has no slides defined.")

    theme = (
        await db.execute(select(Theme).where(Theme.id == template.theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise ValidationError(f"Theme {template.theme_id} not found")

    # Optional role profile from the template
    role_profile_text = ""
    if template.role:
        rp = (
            await db.execute(
                select(RolePromptProfile).where(RolePromptProfile.role == template.role)
            )
        ).scalar_one_or_none()
        if rp:
            role_profile_text = (
                f"\nROLE: {template.role.upper()}\n"
                f"Audience: {rp.audience}\n"
                f"Editorial focus: {rp.focus}\n"
            )

    # Build the per-slide spec for the Gemini prompt
    slide_specs = [
        {
            "order": r.order,
            "title": r.title,
            "layout_type": r.layout_type,
            "prompt_hint": r.prompt_hint,
            "expected_shape": _layout_output_shape(r.layout_type),
        }
        for r in rows
    ]

    prompt = f"""You are filling in the content of a {len(rows)}-slide presentation.

USER PROMPT (overall topic):
\"\"\"{req.prompt.strip()}\"\"\"
{role_profile_text}
TEMPLATE: {template.name}
{template.description or ''}

You will receive a JSON list of slide specs. For each slide, return content
matching the slide's `expected_shape` exactly (keys must match). Hard rules:
- Use the slide's `prompt_hint` as guidance for what that slide should cover.
- Stay grounded in the user's prompt topic.
- For 'bullets' layouts: produce 4–6 concise bullets, no bullet markers in
  the strings (we add them).
- For 'columns' layouts: produce 2 or 3 columns, never more than 3.
- For 'image' layouts: leave `image_url` as an empty string unless you can
  cite a real publicly-available URL (otherwise downstream rendering will
  fall back to a placeholder).
- Keep text concise — slides, not paragraphs.

SLIDE SPECS:
{json.dumps(slide_specs, ensure_ascii=False)}

Return ONLY a JSON object of the form:
{{"slides": [
  {{"order": 1, "content": <object matching slide 1's expected_shape>}},
  {{"order": 2, "content": <object matching slide 2's expected_shape>}},
  ...
]}}

No markdown fences, no commentary."""

    try:
        result = await generate_json(prompt)
    except GeminiError:
        raise
    except Exception as exc:
        raise GeminiError(f"Simple-template generation failed: {exc}") from exc

    contents_by_order: dict[int, dict[str, Any]] = {}
    if isinstance(result, dict):
        for entry in result.get("slides", []) or []:
            if isinstance(entry, dict) and "order" in entry:
                contents_by_order[int(entry["order"])] = entry.get("content", {}) or {}

    # Build the slide JSON shape consumed by the rest of the system
    slides_json: list[dict[str, Any]] = []
    bg_value = (theme.colors or {}).get("background", "#FFFFFF")
    for r in rows:
        content = contents_by_order.get(r.order, {})
        # Always seed `heading` from the template's own slide title if AI omitted it
        if isinstance(content, dict) and not content.get("heading"):
            content["heading"] = r.title
        renderer = _LAYOUT_RENDERERS.get(r.layout_type, _render_bullets)
        slide_id = f"s{r.order}-{uuid4().hex[:6]}"
        slides_json.append(
            {
                "order": r.order,
                "type": r.layout_type if r.layout_type != "image" else "content",
                "background": {"type": "color", "value": bg_value},
                "blocks": renderer(slide_id, content if isinstance(content, dict) else {}),
            }
        )

    title = (req.title or "").strip() or f"{template.name} — {req.prompt[:50]}".strip(" —")

    from app.schemas.presentation import (
        BlockSchema,
        CreatePresentationRequest,
        PositionSchema,
        SlideBackgroundSchema,
        SlideSchema,
        StylingSchema,
    )

    slide_schemas: list[SlideSchema] = []
    for s in slides_json:
        bg = s.get("background")
        blocks = []
        for b in s.get("blocks", []):
            blocks.append(
                BlockSchema(
                    id=b.get("id", ""),
                    type=b.get("type", "text"),
                    content=b.get("content", ""),
                    position=PositionSchema(**(b.get("position") or {"x": 0, "y": 0, "w": 100, "h": 100})),
                    styling=StylingSchema(**(b.get("styling") or {})),
                )
            )
        slide_schemas.append(
            SlideSchema(
                order=s.get("order", 0),
                type=s.get("type", "content"),
                background=SlideBackgroundSchema(**bg) if bg else None,
                blocks=blocks,
            )
        )

    create_req = CreatePresentationRequest(
        title=title,
        description=template.description or "",
        slides=slide_schemas,
        theme_id=str(theme.id),
        template_id=str(template.id),
        token_count=get_last_token_count(),
    )
    return await presentation_service.create_presentation(db, user, create_req)
