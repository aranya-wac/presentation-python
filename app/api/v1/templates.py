from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini_client import generate_json, get_last_token_count
from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models.presentation import Presentation
from app.models.template import Template
from app.models.theme import Theme
from app.models.user import User
from app.schemas.presentation import PresentationDetail
from app.schemas.template import (
    GenerateFromSimpleTemplateRequest,
    PreviewResponse,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateListItem,
    TemplatePublishRequest,
    TemplateUpdateRequest,
)
from app.services import (
    presentation_service,
    simple_template_generation_service,
    template_creation_service,
    template_service,
)

router = APIRouter(prefix="/templates", tags=["templates"])

_PLACEHOLDER_RE = re.compile(r"\[PLACEHOLDER[^\]]*\]", re.IGNORECASE)


@router.get("", response_model=list[TemplateListItem])
async def list_templates(
    category: Optional[str] = Query(None),
    tags: Optional[list[str]] = Query(None),
    source: Optional[str] = Query(
        None,
        description="Filter: 'mine' | 'builtin' | 'all' (omit = all visible)",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TemplateListItem]:
    if source not in (None, "mine", "builtin", "all"):
        source = None
    return await template_service.list_templates(
        db, user=user, category=category, tags=tags, source_filter=source
    )


@router.post("", response_model=TemplateDetail, status_code=status.HTTP_201_CREATED)
async def create_template(
    req: TemplateCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateDetail:
    return await template_creation_service.create_template(db, user, req)


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateDetail:
    return await template_creation_service.get_template_detail(db, user, template_id)


@router.put("/{template_id}", response_model=TemplateDetail)
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateDetail:
    return await template_creation_service.update_template(
        db, user, template_id, req
    )


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await template_creation_service.delete_template(db, user, template_id)


@router.post("/{template_id}/publish", response_model=TemplateDetail)
async def publish_template(
    template_id: str,
    req: TemplatePublishRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateDetail:
    return await template_creation_service.set_published(
        db, user, template_id, req.is_published
    )


@router.post(
    "/{template_id}/generate-simple",
    response_model=PresentationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a presentation from a 'simple' (wizard-built) template",
)
async def generate_simple(
    template_id: str,
    req: GenerateFromSimpleTemplateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    return await simple_template_generation_service.generate_from_simple_template(
        db, user, template_id, req
    )


@router.get("/{template_id}/preview", response_model=PreviewResponse)
async def get_template_preview(
    template_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    """
    Return a Gamma-style rendered preview for a template.
    If a cached preview presentation already exists, return it immediately.
    Otherwise, generate one via the full pipeline and cache it.
    """
    # Load template
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    # Load theme
    theme = (
        await db.execute(select(Theme).where(Theme.id == template.theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme {template.theme_id} not found")

    # Check for existing cached preview
    existing = (
        await db.execute(
            select(Presentation).where(
                Presentation.template_id == template_id,
                Presentation.is_preview == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    if existing:
        slides = existing.slides or []
    else:
        from app.agents.generation.preview_generator_agent import PreviewGeneratorAgent

        agent = PreviewGeneratorAgent()
        try:
            preview = await agent.run(template, theme)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Preview generation failed: {exc}") from exc
        slides = preview.slides or []

    theme_dict = {
        "id": str(theme.id),
        "name": theme.name,
        "colors": theme.colors,
        "fonts": theme.fonts,
    }

    return PreviewResponse(slides=slides, theme=theme_dict)


class GenerateFromPromptRequest(BaseModel):
    prompt: str
    title: Optional[str] = None


def _block_has_placeholder(block: dict) -> bool:
    content = block.get("content", "")
    if not isinstance(content, str):
        return False
    return bool(_PLACEHOLDER_RE.search(content))


async def _get_or_create_preview_slides(
    db: AsyncSession, template: Template, theme: Theme
) -> list[dict[str, Any]]:
    """Return slides from the cached preview presentation, generating one if needed."""
    existing = (
        await db.execute(
            select(Presentation).where(
                Presentation.template_id == template.id,
                Presentation.is_preview == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if existing and existing.slides:
        return copy.deepcopy(existing.slides)

    from app.agents.generation.preview_generator_agent import PreviewGeneratorAgent

    agent = PreviewGeneratorAgent()
    preview = await agent.run(template, theme)
    return copy.deepcopy(preview.slides or [])


@router.post(
    "/{template_id}/generate-from-prompt",
    response_model=PresentationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def generate_from_prompt(
    template_id: str,
    req: GenerateFromPromptRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    """
    Create a new presentation from a template by re-writing the text content
    of the preview slides based on the user's prompt. Layout, styling, slide
    order, block IDs, positions, and slide count are preserved exactly —
    only block text content changes. The preview rendering shown to the user
    on the Create-from-Template screen is the same source of slides used here,
    so the generated deck visually matches what the user previewed.
    """
    prompt_text = (req.prompt or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    theme = (
        await db.execute(select(Theme).where(Theme.id == template.theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme {template.theme_id} not found")

    # Use the SAME slides shown to the user in the preview so the generated
    # deck visually matches what they saw.
    slides: list[dict[str, Any]] = await _get_or_create_preview_slides(db, template, theme)

    # Collect every text-bearing block. We rewrite text only — never positions,
    # styling, slide order, block IDs, or block types.
    slots: list[dict[str, Any]] = []
    text_block_types = {
        "title", "subtitle", "heading", "caption", "text", "bullet", "bullets",
        "body", "quote", "stat", "stats", "swot", "persona", "label",
    }
    for slide in slides:
        slide_type = slide.get("type", "content")
        for block in slide.get("blocks", []):
            btype = (block.get("type") or "").lower()
            content = block.get("content", "")
            if btype == "image":
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if btype and btype not in text_block_types and not _block_has_placeholder(block):
                # unknown block types — skip unless they have placeholder markers
                continue
            slots.append(
                {
                    "id": block.get("id", ""),
                    "slide_order": slide.get("order", 0),
                    "slide_type": slide_type,
                    "block_type": block.get("type", "text"),
                    "current": content,
                }
            )

    if slots:
        ai_prompt = f"""You are rewriting the text content of an existing presentation so it matches a new topic.

The user described what the presentation should be about:
\"\"\"{prompt_text}\"\"\"

Template: {template.name}
Template description: {template.description or ''}

Below is a JSON list of text blocks from the presentation. Each block has:
- "id": the block id (echo this exact id in your response)
- "slide_order": which slide it lives on (1-indexed)
- "slide_type": the slide's role (title, agenda, content, stats, closing, etc.)
- "block_type": the block role (title, subtitle, heading, bullets, stat, caption, etc.)
- "current": the current text in that block

Rewrite the "current" text of every block so the whole deck tells a coherent
story about the user's prompt. Hard rules:
- Keep approximately the same length, line count, and structure as the current text.
- If the current text uses bullets / multiple lines / labels (e.g. "STRENGTHS\\n..."),
  keep that exact shape — same number of bullets, same labels.
- Replace any bracketed placeholder markers like [PLACEHOLDER: ...], [your_email],
  [Channel 1], [$XXX,XXX], [LOGO_PLACEHOLDER], [Name], [Author], etc. with concrete
  content relevant to the user's prompt. Do NOT keep brackets in the output.
- Keep proper nouns / company names from the prompt; invent plausible numbers and
  details only when the prompt does not provide them.
- Do NOT add or remove blocks. Do NOT change slide order.

Blocks:
{json.dumps(slots, ensure_ascii=False)}

Return a JSON object with a single key "replacements" mapping block id to the
new text. Every input id must appear in the output. Example:
{{"replacements": {{"s1-title": "Acme Corp Growth Strategy", "s1-subtitle": "..."}}}}

Return ONLY valid JSON. No markdown fences, no commentary."""

        try:
            result = await generate_json(ai_prompt)
            token_count = get_last_token_count()
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"AI generation failed: {exc}"
            ) from exc
    else:
        token_count = 0

        replacements = (result or {}).get("replacements", {}) if isinstance(result, dict) else {}
        if not isinstance(replacements, dict):
            replacements = {}

        # Apply replacements + scrub any leftover bracket markers as a safety net
        for slide in slides:
            for block in slide.get("blocks", []):
                bid = block.get("id", "")
                if bid in replacements and isinstance(replacements[bid], str):
                    block["content"] = replacements[bid]
                # Safety net: strip any remaining "[PLACEHOLDER...]" markers.
                content = block.get("content", "")
                if isinstance(content, str) and "[PLACEHOLDER" in content.upper():
                    block["content"] = _PLACEHOLDER_RE.sub("", content).strip()

    # Title: use user-supplied or derive from prompt
    title = (req.title or "").strip() or f"{template.name} — {prompt_text[:50]}".strip(" —")

    # Build a CreatePresentationRequest-equivalent and persist via service so
    # validation / serialization stays consistent.
    from app.schemas.presentation import (
        CreatePresentationRequest,
        SlideBackgroundSchema,
        SlideSchema,
        BlockSchema,
        PositionSchema,
        StylingSchema,
    )

    slide_schemas: list[SlideSchema] = []
    for s in slides:
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
        token_count=token_count,
    )

    return await presentation_service.create_presentation(db, current_user, create_req)
