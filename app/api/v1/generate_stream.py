from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.prompt_templates import (
    COMBINED_GENERATION_PROMPT,
    OUTLINE_ONLY_PROMPT,
    level_instructions,
    render,
)
from app.api.dependencies import get_current_user
from app.api.v1.generate_sync import _extract_file_text, _extract_url_text
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.theme import Theme
from app.models.user import User
from app.utils.logger import get_logger
from app.utils.validators import extract_slide_count_from_prompt

router = APIRouter(prefix="/generate", tags=["generation"])
logger = get_logger(__name__)


def _sse(event: str, data: dict | list | str) -> str:
    """Format a Server-Sent Event line."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def _stream_generation(
    prompt: str,
    slide_count: int,
    file_text: str,
    url_text: str,
    images: list[tuple[bytes, str]],
    db: AsyncSession,
    level: str = "simple",
    user_outline: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Drive generation step-by-step, emitting SSE events as work progresses."""
    from app.agents.generation.preview_generator_agent import _build_outline
    from app.agents.generation.slide_generator_agent import (
        SlideGeneratorAgent,
        _layout_blocks,
        _content_to_blocks,
        _slide_background,
        _system_layout,
    )
    from app.agents.generation.template_mapper_agent import TemplateMappingResult

    yield _sse("status", {"step": "analyzing", "message": "Reading your source material…"})

    parts: list[str] = []
    if prompt:
        parts.append(prompt)
    if file_text:
        parts.append(file_text)
    if url_text:
        parts.append(url_text)
    content = "\n\n".join(parts) if parts else "Use the attached image(s) as the primary source."
    if not prompt:
        prompt = "Create a presentation from the provided source material."

    normalized_level = "advanced" if (level or "").lower() == "advanced" else "simple"
    # When a user-confirmed outline is provided, inject it as a hard constraint so
    # Gemini writes content that matches the titles/types the user already approved.
    outline_constraint = ""
    if user_outline:
        outline_constraint = "\n\nUser-confirmed outline — generate slides for EXACTLY these titles and types, in this order:\n"
        for s in user_outline:
            outline_constraint += f"  {s.get('order', '?')}. [{s.get('type', 'content')}] {s.get('title', '')}\n"
        outline_constraint += (
            "Each slide's 'type' field must match the bracketed type above. "
            "The slide's 'heading' should be the corresponding title (you may polish wording slightly).\n"
        )
    combined_prompt = render(
        COMBINED_GENERATION_PROMPT,
        prompt=prompt + outline_constraint,
        content=content,
        slide_count=slide_count,
        level=normalized_level,
        level_instructions=level_instructions(normalized_level),
    )
    if images:
        combined_prompt += (
            "\n\nThe attached images are part of the source material. "
            "Read text/charts/data from them and use those facts in slides."
        )

    try:
        if images:
            result = await gemini_client.generate_json_multimodal(combined_prompt, images)
        else:
            result = await gemini_client.generate_json(combined_prompt)
    except Exception as exc:
        logger.error(f"Streaming generation: analysis failed: {exc}")
        yield _sse("error", {"message": f"Generation failed: {exc}"})
        return

    analysis = {
        "title": result.get("title", prompt[:60]),
        "summary": result.get("summary", prompt),
        "audience": result.get("audience", "General audience"),
        "tone": result.get("tone", "professional"),
        "estimated_slides": slide_count,
        "sections": result.get("sections", []),
    }
    yield _sse("analysis", analysis)

    if user_outline:
        # Use the user-confirmed outline directly. Types stay as the user set them.
        outline = [
            {
                "order": int(s.get("order", i + 1) or i + 1),
                "type": str(s.get("type", "content") or "content").lower(),
                "title": str(s.get("title", f"Slide {i + 1}") or "").strip(),
                "key_points": [],
            }
            for i, s in enumerate(user_outline)
        ]
    else:
        outline = _build_outline(analysis, target_slide_count=slide_count)
    yield _sse("outline", {"slide_count": len(outline), "titles": [o.get("title", "") for o in outline]})

    # Theme: load default
    theme = (await db.execute(select(Theme))).scalars().first()
    if not theme:
        yield _sse("error", {"message": "No theme found"})
        return
    theme_dict = {
        "id": str(theme.id),
        "name": theme.name,
        "colors": theme.colors,
        "fonts": theme.fonts,
    }
    yield _sse("theme", theme_dict)

    # Use slide contents from the analysis call (single-call mode) and just emit
    # them one-by-one with the layout applied. This keeps the API call count to
    # one while still letting the UI render progressively.
    # If Gemini returned something unexpected (e.g. a list, or a dict without
    # "slides"), surface that as an SSE error instead of silently throwing
    # halfway through and leaving the frontend with "Stream ended without
    # producing any slides."
    if not isinstance(result, dict):
        yield _sse("error", {"message": f"Gemini returned unexpected payload type: {type(result).__name__}"})
        return
    contents: list[dict] = result.get("slides") or []
    if not isinstance(contents, list):
        yield _sse("error", {"message": "Gemini 'slides' was not a list"})
        return

    empty = {
        "heading": "", "body": "", "bullets": [], "stats": [], "quote": "", "caption": "",
        "chart": {}, "roadmap": [], "comparison": {}, "columns": [], "funnel": [],
        "image_prompt": "", "notes": "",
    }
    while len(contents) < len(outline):
        contents.append(dict(empty))
    contents = contents[: len(outline)]

    # Slide types Gemini may emit that the outline doesn't know about. When the
    # LLM returns a richer type for a content slide, honor it so chart/roadmap/
    # quote rendering actually fires.
    RICH_TYPES = {"chart", "roadmap", "quote", "stats", "comparison", "kanban", "funnel"}

    # Image generation: SERIALIZED through a semaphore (concurrency=1) to stay
    # under the gemini-2.5-flash-image free-tier RPM limit (~5/min). Firing 5
    # in parallel reliably hit 429 on the image model even when text was fine.
    # We still kick off the tasks during the slide loop so they begin early,
    # but only one image actually contacts Gemini at a time.
    MAX_IMAGES_PER_DECK = 5
    _image_semaphore = asyncio.Semaphore(1)
    IMAGE_STYLE_SUFFIX = (
        ". Editorial photograph, soft natural light, shallow depth of field, "
        "muted desaturated palette, clean composition, professional, no text overlay."
    )
    image_tasks: list[tuple[int, asyncio.Task]] = []  # (slide_idx, asyncio Task → str | None)

    # Lazy imports — only paid if advanced mode actually asks for images.
    async def _spawn_image_task(idx: int, prompt_text: str) -> asyncio.Task:
        from app.ai.gemini_client import generate_image
        from app.api.v1.images import GENERATED_DIR
        import uuid

        async def _one() -> str | None:
            # Serialize: only one image hits Gemini at a time. With ~12s/image
            # on the image model, 5 images cost ~60s wall time but reliably
            # complete instead of all returning 429.
            async with _image_semaphore:
                try:
                    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                    full_prompt = prompt_text + IMAGE_STYLE_SUFFIX
                    img_bytes, mime = await asyncio.wait_for(generate_image(full_prompt), timeout=40.0)
                except Exception as exc:
                    logger.warning(f"image_prompt failed for slide {idx}: {str(exc)[:200]}")
                    return None
                ext = mime.split("/")[-1] if "/" in mime else "png"
                if ext == "jpeg":
                    ext = "jpg"
                fname = f"{uuid.uuid4()}.{ext}"
                (GENERATED_DIR / fname).write_bytes(img_bytes)
                # Small spacing between successive images to stay below per-minute RPM.
                await asyncio.sleep(2.0)
                return f"/generated/{fname}"
        return asyncio.create_task(_one())

    def _image_block(slide_idx: int, url: str) -> dict:
        return {
            "id": f"img-{slide_idx}",
            "type": "image",
            "content": url,
            "position": {"x": 720, "y": 180, "w": 500, "h": 460},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": "transparent", "background_color": "transparent", "text_align": "left",
            },
        }

    theme_colors = theme.colors
    theme_fonts = theme.fonts
    content_idx = 0
    for i, (outline_item, slide_content) in enumerate(zip(outline, contents)):
        try:
            if not isinstance(slide_content, dict):
                slide_content = dict(empty)
            outline_type = outline_item.get("type", "content")
            llm_type = (slide_content.get("type") or "").strip().lower()
            # title/agenda are always pinned. closing yields to a richer LLM
            # type so a roadmap/chart placed in the last middle slot isn't
            # replaced by the generic closing layout. Middle slides prefer
            # the LLM type when richer.
            if outline_type in ("title", "agenda"):
                slide_type = outline_type
            elif outline_type == "closing":
                slide_type = llm_type if llm_type in RICH_TYPES else "closing"
            elif llm_type in RICH_TYPES:
                slide_type = llm_type
            else:
                slide_type = outline_type
            # Downgrade: if Gemini picked a structured type but didn't supply
            # the data to back it (empty chart_data / comparison items / etc.),
            # fall back to plain content so we don't render empty panels.
            from app.agents.generation.slide_generator_agent import _has_structured_data
            if slide_type in RICH_TYPES and not _has_structured_data(slide_type, slide_content):
                logger.info(f"Slide {i}: '{slide_type}' had no data — downgrading to content")
                slide_type = "content"
                # If there are no bullets either, seed one from the heading
                # so the slide doesn't render completely empty.
                if not slide_content.get("bullets") and not slide_content.get("body"):
                    h = slide_content.get("heading") or outline_item.get("title", "")
                    if h:
                        slide_content["bullets"] = [h]
            slide_layout = _system_layout(slide_type, slide_content, slide_index=content_idx)
            if slide_type not in ("title", "closing"):
                content_idx += 1

            gen_blocks = _content_to_blocks(slide_content, slide_type)
            blocks = _layout_blocks(slide_type, slide_layout, gen_blocks, theme_colors, theme_fonts)
            background = _slide_background(slide_type, slide_layout, "", theme_colors)

            slide = {
                "order": outline_item.get("order", i + 1),
                "type": slide_type,
                "background": background,
                "blocks": blocks,
                "notes": str(slide_content.get("notes") or "").strip(),
            }
        except Exception as exc:
            # One bad slide should not kill the whole stream. Emit a minimal
            # placeholder so the deck still completes; log for debugging.
            logger.exception(f"Slide {i} render failed: {exc}")
            slide = {
                "order": outline_item.get("order", i + 1),
                "type": "content",
                "background": {"type": "color", "value": theme.colors.get("background", "#FFFFFF")},
                "blocks": [{
                    "id": f"err-{i}", "type": "heading",
                    "content": outline_item.get("title", f"Slide {i + 1}"),
                    "position": {"x": 60, "y": 280, "w": 1160, "h": 160},
                    "styling": {
                        "font_family": theme.fonts.get("heading", {}).get("family", "Inter"),
                        "font_size": 36, "font_weight": 700,
                        "color": theme.colors.get("primary", "#000"),
                        "background_color": "transparent", "text_align": "center",
                    },
                }],
                "notes": "",
            }
        yield _sse("slide", {"index": i, "total": len(outline), "slide": slide})

        # Spawn an image generation task immediately if this slide asked for one
        # (advanced mode only, capped). The task runs while subsequent slides
        # stream — we drain completed images after the loop ends.
        if normalized_level == "advanced" and len(image_tasks) < MAX_IMAGES_PER_DECK:
            img_prompt = str(slide_content.get("image_prompt") or "").strip()
            # Force a hero image on the title slide for advanced decks if
            # Gemini didn't already provide one — biggest visible quality lift.
            if not img_prompt and i == 0 and slide_type == "title":
                # Use the deck title/heading as the basis for the hero image.
                heading_text = str(slide_content.get("heading") or analysis.get("title") or "").strip()
                if heading_text:
                    img_prompt = f"A striking hero image illustrating: {heading_text}"
            if img_prompt:
                task = await _spawn_image_task(i, img_prompt)
                image_tasks.append((i, task))

        # Drain any completed image tasks between slide yields — this lets
        # users see the image appear within the same flow, not after.
        for slide_idx, task in image_tasks:
            if task.done() and not getattr(task, "_drained", False):
                task._drained = True  # mark so we don't double-emit
                url = await task
                if url:
                    yield _sse("slide_image", {"index": slide_idx, "block": _image_block(slide_idx, url)})

        # tiny pause so the browser actually flushes between events
        await asyncio.sleep(0.02)

    # After all slides have streamed, drain any still-running image tasks.
    # Poll-wait so each image yields as soon as IT finishes, not the slowest.
    pending = [(idx, t) for idx, t in image_tasks if not getattr(t, "_drained", False)]
    if pending:
        yield _sse("status", {"step": "imaging", "message": f"Finalizing {len(pending)} image(s)…"})
        while pending:
            done_now = [(idx, t) for idx, t in pending if t.done()]
            if done_now:
                for slide_idx, t in done_now:
                    t._drained = True
                    url = await t
                    if url:
                        yield _sse("slide_image", {"index": slide_idx, "block": _image_block(slide_idx, url)})
                        await asyncio.sleep(0.02)
                pending = [(idx, t) for idx, t in pending if not getattr(t, "_drained", False)]
            else:
                # No image ready yet — wait a beat without burning CPU.
                await asyncio.sleep(0.25)

    usage = gemini_client.last_token_usage or {}
    yield _sse("complete", {
        "slide_count": len(outline),
        "token_count": usage.get("total", 0),
        "prompt_tokens": usage.get("prompt", 0),
        "completion_tokens": usage.get("completion", 0),
    })


@router.post("/stream")
@limiter.limit("20/hour")
async def generate_stream(
    request: Request,
    prompt: str = Form(""),
    slide_count: int = Form(10),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    images: list[UploadFile] = File(default=[]),
    level: str = Form("simple"),
    outline_json: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events endpoint that streams slides as they're produced.

    Events: status, analysis, outline, theme, slide (one per slide), complete, error.

    When `outline_json` is provided (a JSON array of {order, type, title}), the
    analysis call is still made for theme/audience context, but Gemini is
    constrained to produce slides matching the user-confirmed outline.
    """
    prompt_slide_count = extract_slide_count_from_prompt(prompt)
    if prompt_slide_count is not None:
        slide_count = prompt_slide_count
    slide_count = max(5, min(20, slide_count))

    file_text = ""
    if file and file.filename:
        try:
            file_text = await _extract_file_text(file)
            if len(file_text) > 50_000:
                file_text = file_text[:50_000] + "\n...[content truncated]"
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"File extraction failed, ignoring file: {exc}")

    url_text = ""
    url_value = (url or "").strip()
    if url_value:
        title, page_text = await _extract_url_text(url_value)
        if len(page_text) > 50_000:
            page_text = page_text[:50_000] + "\n...[content truncated]"
        url_text = f"Source: {url_value}\nTitle: {title}\n\n{page_text}"

    image_payloads: list[tuple[bytes, str]] = []
    MAX_IMAGES = 4
    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    for img in (images or [])[:MAX_IMAGES]:
        if not img or not img.filename:
            continue
        mime = (img.content_type or "image/png").lower()
        if not mime.startswith("image/"):
            continue
        data = await img.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail=f"Image '{img.filename}' exceeds 5 MB limit.")
        if data:
            image_payloads.append((data, mime))

    if not (prompt or file_text or url_text or image_payloads):
        raise HTTPException(
            status_code=400,
            detail="Provide a prompt, a document, a URL, or at least one image.",
        )

    # Parse the optional user-confirmed outline. Bad JSON or wrong shape
    # falls back to None (auto-outline) rather than 500-ing the request.
    user_outline: list[dict] | None = None
    if outline_json:
        try:
            parsed = json.loads(outline_json)
            if isinstance(parsed, list) and all(isinstance(s, dict) for s in parsed):
                user_outline = parsed
            else:
                logger.warning("outline_json was not a list of objects; ignoring")
        except json.JSONDecodeError as exc:
            logger.warning(f"outline_json was not valid JSON ({exc}); ignoring")

    return StreamingResponse(
        _stream_generation(prompt, slide_count, file_text, url_text, image_payloads, db, level, user_outline),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx, etc)
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Outline-first generation: fast plan call that lets the user edit the
# deck structure before paying for full slide content generation.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/outline")
@limiter.limit("60/hour")
async def generate_outline(
    request: Request,
    prompt: str = Form(""),
    slide_count: int = Form(10),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    images: list[UploadFile] = File(default=[]),
    level: str = Form("simple"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return just the deck outline (title + slide titles + types).

    No slide content is generated — that comes later via /generate/stream
    with the user-confirmed outline.
    """
    slide_count = max(1, min(30, slide_count))
    normalized_level = "advanced" if (level or "").lower() == "advanced" else "simple"

    file_text = ""
    if file and file.filename:
        try:
            file_text = await _extract_file_text(file)
            if len(file_text) > 50_000:
                file_text = file_text[:50_000] + "\n...[content truncated]"
        except Exception as exc:
            logger.warning(f"File extraction failed, ignoring file: {exc}")

    url_text = ""
    url_value = (url or "").strip()
    if url_value:
        title, page_text = await _extract_url_text(url_value)
        if len(page_text) > 50_000:
            page_text = page_text[:50_000] + "\n...[content truncated]"
        url_text = f"Source: {url_value}\nTitle: {title}\n\n{page_text}"

    image_payloads: list[tuple[bytes, str]] = []
    for img in (images or [])[:4]:
        if not img or not img.filename:
            continue
        mime = (img.content_type or "image/png").lower()
        if not mime.startswith("image/"):
            continue
        data = await img.read()
        if data and len(data) <= 5 * 1024 * 1024:
            image_payloads.append((data, mime))

    if not (prompt or file_text or url_text or image_payloads):
        raise HTTPException(
            status_code=400,
            detail="Provide a prompt, a document, a URL, or at least one image.",
        )

    parts: list[str] = []
    if prompt:    parts.append(prompt)
    if file_text: parts.append(file_text)
    if url_text:  parts.append(url_text)
    content = "\n\n".join(parts) if parts else "Use the attached image(s) as the primary source."

    outline_prompt = render(
        OUTLINE_ONLY_PROMPT,
        prompt=prompt or "Create a presentation from the provided source material.",
        content=content,
        slide_count=slide_count,
        level=normalized_level,
    )

    try:
        if image_payloads:
            result = await gemini_client.generate_json_multimodal(outline_prompt, image_payloads)
        else:
            result = await gemini_client.generate_json(outline_prompt)
    except Exception as exc:
        logger.error(f"outline generation failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Outline failed: {exc}")

    # Validate / pad / truncate the slides list to exactly slide_count entries.
    raw_slides = result.get("slides") or []
    slides_out: list[dict] = []
    for i, s in enumerate(raw_slides[:slide_count]):
        if not isinstance(s, dict):
            continue
        slides_out.append({
            "order": int(s.get("order", i + 1) or i + 1),
            "type": str(s.get("type", "content")).lower(),
            "title": str(s.get("title", f"Slide {i + 1}")).strip(),
        })
    while len(slides_out) < slide_count:
        slides_out.append({
            "order": len(slides_out) + 1,
            "type": "content",
            "title": f"Slide {len(slides_out) + 1}",
        })
    # Enforce structural pins.
    if slides_out:
        slides_out[0]["type"] = "title"
    if slide_count >= 2:
        slides_out[1]["type"] = "agenda"
    if slide_count >= 3:
        slides_out[-1]["type"] = "closing"

    usage = gemini_client.last_token_usage or {}
    return {
        "deck_title": result.get("title", ""),
        "summary": result.get("summary", ""),
        "audience": result.get("audience", ""),
        "tone": result.get("tone", "professional"),
        "sections": result.get("sections", []),
        "slides": slides_out,
        "token_count": usage.get("total", 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-slide regenerate
# ─────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel
from string import Template
from app.ai.prompt_templates import render as _render


class RegenerateSlideRequest(BaseModel):
    original_prompt: str
    level: str = "simple"
    slide_type: str           # title | agenda | content | stats | quote | chart | roadmap | closing
    slide_title: str = ""     # current slide heading, gives the LLM steering context
    deck_titles: list[str] = []   # all slide titles in the deck, for coherence
    instruction: str = ""     # optional user nudge: "make it more concise", "add Q4 numbers", etc.


_REGEN_SLIDE_PROMPT = Template("""
You are refining a single slide in an existing presentation. Regenerate ONLY this slide.

Original user prompt for the whole deck:
$original_prompt

Deck context (all slide titles in order):
$deck_titles

The slide to regenerate:
- Position: slide titled "$slide_title"
- Required type: $slide_type
- Required level: $level

User refinement instruction (optional, may be empty):
$instruction

$level_instructions

Return ONLY this JSON for the single slide (no markdown, no explanation):
{
  "type": "$slide_type",
  "heading": "Slide heading (≤8 words)",
  "body": "",
  "bullets": ["bullet 1", "bullet 2"],
  "stats": [],
  "quote": "",
  "caption": "",
  "chart": { "type": "bar|line|pie", "data": [{"label": "Q1", "value": 12}] },
  "roadmap": [{"phase": "Q1 2026", "label": "Launch"}],
  "image_prompt": "",
  "notes": "Speaker notes (2–3 sentences, ≤80 words)"
}

Rules:
- The "type" field must equal "$slide_type".
- Use chart only when slide_type=="chart"; roadmap only when slide_type=="roadmap"; stats only when slide_type=="stats"; quote only when slide_type=="quote".
- bullets empty for title, closing, chart, and roadmap.
- notes: 2–3 sentences of natural spoken language. Do NOT just re-list the bullets. ≤80 words.
- All fields always present; use "", [], or {} when not applicable.
- Never invent facts absent from the original prompt.
""")


@router.post("/slide")
@limiter.limit("60/hour")
async def regenerate_slide(
    request: Request,
    payload: RegenerateSlideRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Regenerate a single slide and return its rendered blocks.

    Returns: { "slide": { type, background, blocks }, "token_count": int }
    """
    from app.agents.generation.slide_generator_agent import (
        _content_to_blocks, _layout_blocks, _slide_background, _system_layout,
    )

    normalized_level = "advanced" if (payload.level or "").lower() == "advanced" else "simple"
    requested_type = (payload.slide_type or "content").lower()
    allowed = {
        "title", "agenda", "content", "stats", "quote",
        "chart", "roadmap", "comparison", "kanban", "funnel", "closing",
    }
    if requested_type not in allowed:
        raise HTTPException(status_code=400, detail=f"slide_type must be one of {sorted(allowed)}")

    prompt_text = _render(
        _REGEN_SLIDE_PROMPT,
        original_prompt=payload.original_prompt or "",
        deck_titles="\n".join(f"- {t}" for t in payload.deck_titles) if payload.deck_titles else "(unknown)",
        slide_title=payload.slide_title or "",
        slide_type=requested_type,
        level=normalized_level,
        instruction=payload.instruction or "(no extra instruction)",
        level_instructions=level_instructions(normalized_level),
    )

    try:
        slide_content = await gemini_client.generate_json(prompt_text)
    except Exception as exc:
        logger.error(f"regenerate_slide failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Regeneration failed: {exc}")

    if not isinstance(slide_content, dict):
        raise HTTPException(status_code=502, detail="LLM returned non-dict slide content")

    theme = (await db.execute(select(Theme))).scalars().first()
    if not theme:
        raise HTTPException(status_code=500, detail="No theme found")

    slide_type = requested_type
    slide_layout = _system_layout(slide_type, slide_content, slide_index=0)
    gen_blocks = _content_to_blocks(slide_content, slide_type)
    blocks = _layout_blocks(slide_type, slide_layout, gen_blocks, theme.colors, theme.fonts)
    background = _slide_background(slide_type, slide_layout, "", theme.colors)

    usage = gemini_client.last_token_usage or {}
    return {
        "slide": {
            "type": slide_type,
            "background": background,
            "blocks": blocks,
            "notes": str(slide_content.get("notes") or "").strip(),
        },
        "token_count": usage.get("total", 0),
    }
