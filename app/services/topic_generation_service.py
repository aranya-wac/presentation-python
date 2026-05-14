from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.prompt_templates import (
    RESEARCH_SUMMARY_PROMPT,
    TOPIC_OUTLINE_PROMPT,
    level_instructions,
    render,
)
from app.models.theme import Theme
from app.services import article_extractor, news_search
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _sse(event: str, data) -> str:
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


def _format_articles_block(articles: list[article_extractor.Article]) -> str:
    """Render articles as a numbered prompt block. Numbers are 1-indexed and
    must match what the AI cites back."""
    lines: list[str] = []
    for i, a in enumerate(articles, 1):
        lines.append(f"=== Article [{i}] ===")
        lines.append(f"Title: {a.title}")
        lines.append(f"Source: {a.source}")
        lines.append(f"URL: {a.url}")
        lines.append("")
        lines.append(a.truncated)
        lines.append("")
    return "\n".join(lines)


async def stream_generation(
    topic: str,
    audience: str,
    style: str,
    slide_count: int,
    depth: str,
    db: AsyncSession,
    level: str = "simple",
) -> AsyncIterator[str]:
    """Drive the topic-driven generation pipeline as an SSE stream."""
    from app.agents.generation.preview_generator_agent import _build_outline as _legacy_build_outline  # noqa: F401
    from app.agents.generation.slide_generator_agent import (
        _content_to_blocks,
        _layout_blocks,
        _slide_background,
        _system_layout,
    )

    topic = (topic or "").strip()
    if not topic:
        yield _sse("error", {"message": "Topic is required."})
        return

    # ── 1. Search ────────────────────────────────────────────────────────────
    yield _sse("status", {"step": "searching", "message": f"Searching news for '{topic}'…"})
    try:
        hits = await news_search.search(topic, depth=depth)
    except Exception as exc:
        logger.exception("News search failed")
        yield _sse("error", {"message": f"Search failed: {exc}"})
        return

    if not hits:
        from app.config import settings as _settings
        if not _settings.SERPER_API_KEY:
            yield _sse("error", {
                "message": (
                    "No articles found. The free DuckDuckGo fallback is unreliable "
                    "(anti-bot interstitials). Set SERPER_API_KEY in backend/.env "
                    "for production-grade search, or retry in a moment."
                ),
            })
        else:
            yield _sse("error", {
                "message": f"No articles found for '{topic}'. Try a broader topic.",
            })
        return

    yield _sse("search_results", {
        "count": len(hits),
        "sources": [{"title": h.title, "url": h.url, "source": h.source} for h in hits],
    })

    # ── 2. Extract ───────────────────────────────────────────────────────────
    yield _sse("status", {"step": "extracting", "message": "Reading articles…"})
    items = [(h.url, h.title, h.source, h.snippet) for h in hits]
    articles = await article_extractor.fetch_many(items, max_concurrency=4)
    yield _sse("extracted", {"requested": len(items), "succeeded": len(articles)})

    if len(articles) == 0:
        yield _sse("error", {"message": "Could not read any of the articles found. They may be paywalled or blocked."})
        return
    if len(articles) < 2:
        yield _sse("warning", {
            "message": f"Only {len(articles)} article extracted. Continuing, but the deck may lean on limited sourcing."
        })

    # ── 3. Research summary ─────────────────────────────────────────────────
    yield _sse("status", {"step": "synthesizing", "message": "Synthesizing research…"})
    summary_prompt = render(
        RESEARCH_SUMMARY_PROMPT,
        topic=topic,
        audience=audience or "General audience",
        style=style or "professional",
        articles=_format_articles_block(articles),
    )
    try:
        brief = await gemini_client.generate_json(summary_prompt)
        token_count = gemini_client.get_last_token_count()
    except Exception as exc:
        logger.exception("Research summary failed")
        msg = str(exc)
        if "503" in msg or "Service Unavailable" in msg or "overloaded" in msg.lower():
            user_msg = (
                "Gemini is overloaded right now (503). The retry-with-backoff "
                "couldn't get through. Try again in a minute — or use Quick scan / "
                "Standard depth to send a smaller prompt."
            )
        elif "429" in msg or "Quota" in msg:
            user_msg = "Gemini quota exceeded. Wait a minute or check your API key's quota in Google AI Studio."
        else:
            user_msg = f"Research synthesis failed: {exc}"
        yield _sse("error", {"message": user_msg})
        return

    # Augment with the source URLs (positional) so the frontend always has them
    # even if the model forgot to populate `sources_used`.
    brief["_articles"] = [
        {"index": i + 1, "title": a.title, "url": a.url, "source": a.source}
        for i, a in enumerate(articles)
    ]
    yield _sse("research", brief)

    # ── 4. Outline ──────────────────────────────────────────────────────────
    yield _sse("status", {"step": "planning", "message": "Drafting slide outline…"})
    slide_count = max(5, min(20, int(slide_count or 10)))

    outline_prompt = render(
        TOPIC_OUTLINE_PROMPT,
        topic=topic,
        audience=audience or "General audience",
        style=style or "professional",
        slide_count=slide_count,
        brief=json.dumps(brief, indent=2),
    )
    # Bias the outline planner toward stats / charts / process / image slides
    # when the user picked Advanced.
    outline_prompt += "\n\n" + level_instructions(level)
    try:
        outline_raw = await gemini_client.generate_json(outline_prompt)
        token_count += gemini_client.get_last_token_count()
    except Exception as exc:
        logger.exception("Outline generation failed")
        yield _sse("error", {"message": f"Outline failed: {exc}"})
        return

    # Outline can come back as either a list or {"slides":[...]}
    if isinstance(outline_raw, dict):
        outline = outline_raw.get("slides") or outline_raw.get("outline") or []
    else:
        outline = outline_raw or []

    if not outline:
        yield _sse("error", {"message": "AI returned no outline."})
        return

    yield _sse("outline", {
        "slide_count": len(outline),
        "titles": [item.get("title", "") for item in outline],
    })

    # ── 5. Theme ────────────────────────────────────────────────────────────
    theme = (await db.execute(select(Theme))).scalars().first()
    if not theme:
        yield _sse("error", {"message": "No theme found in database."})
        return
    yield _sse("theme", {
        "id": str(theme.id),
        "name": theme.name,
        "colors": theme.colors,
        "fonts": theme.fonts,
    })

    # ── 6. Slides ───────────────────────────────────────────────────────────
    theme_colors = theme.colors
    theme_fonts = theme.fonts
    content_idx = 0

    # Image generation infra (advanced level only). Mirrors the prompt-mode
    # stream: max 5 images per deck, serialized to 1 concurrent gen so we
    # stay under the gemini-2.5-flash-image RPM cap.
    normalized_level = (level or "simple").strip().lower()
    MAX_IMAGES_PER_DECK = 5
    _image_semaphore = asyncio.Semaphore(1)
    IMAGE_STYLE_SUFFIX = (
        ". Editorial photograph, soft natural light, shallow depth of field, "
        "muted desaturated palette, clean composition, professional, no text overlay."
    )
    image_tasks: list[tuple[int, "asyncio.Task[str | None]"]] = []

    async def _spawn_image_task(idx: int, prompt_text: str) -> "asyncio.Task[str | None]":
        from app.ai.gemini_client import generate_image
        from app.api.v1.images import GENERATED_DIR
        import uuid

        async def _one() -> str | None:
            async with _image_semaphore:
                try:
                    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                    full_prompt = prompt_text + IMAGE_STYLE_SUFFIX
                    img_bytes, mime = await asyncio.wait_for(generate_image(full_prompt), timeout=40.0)
                except Exception as exc:
                    logger.warning(f"topic image_prompt failed for slide {idx}: {str(exc)[:200]}")
                    return None
                ext = mime.split("/")[-1] if "/" in mime else "png"
                if ext == "jpeg":
                    ext = "jpg"
                fname = f"{uuid.uuid4()}.{ext}"
                (GENERATED_DIR / fname).write_bytes(img_bytes)
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

    # Slide types that benefit from a generated photograph. Excludes:
    # agenda (text list), sources (URL list), stats/chart/comparison/etc
    # (the data is the visual), and closing (often a CTA).
    _IMAGE_OK_TYPES = {"title", "content", "quote", "timeline"}

    def _derive_image_prompt(slide_type: str, slide_content: dict) -> str:
        """Build a short prompt for the image model from the slide content.

        Topic-mode generation doesn't ask Gemini for image_prompts inline (the
        research/outline prompts focus on factual fidelity). We synthesize
        them deterministically from the slide title/heading so the call is
        free and deterministic.
        """
        heading = (slide_content.get("heading") or "").strip()
        if not heading:
            return ""
        if slide_type == "title":
            return f"A striking hero image illustrating: {heading}"
        return f"An editorial illustration of: {heading}, in the context of {topic}"

    # Pre-compute content payload per outline item using the brief.
    for i, item in enumerate(outline):
        slide_type = item.get("type") or "content"
        title_text = item.get("title") or topic
        key_points = item.get("key_points") or []

        if slide_type == "title":
            content = {
                "heading": brief.get("title") or topic,
                "body": brief.get("overview", ""),
                "bullets": [], "stats": [], "quote": "", "caption": "",
            }
        elif slide_type == "agenda":
            content = {
                "heading": "Agenda",
                "body": "",
                "bullets": [it.get("title", "") for it in outline if it.get("type") not in ("title", "agenda")][:6],
                "stats": [], "quote": "", "caption": "",
            }
        elif slide_type == "stats":
            stats_payload = brief.get("statistics") or []
            stats_strs = [
                f"{s.get('value','')} {s.get('label','')}".strip()
                for s in stats_payload[:4] if s.get("value")
            ]
            content = {
                "heading": title_text,
                "body": "",
                "bullets": [], "stats": stats_strs,
                "quote": "", "caption": "",
            }
        elif slide_type == "timeline":
            tl = brief.get("timeline") or []
            bullets = [f"{t.get('date','')}: {t.get('event','')}".strip(": ") for t in tl[:5]]
            content = {
                "heading": title_text or "Timeline",
                "body": "",
                "bullets": bullets,
                "stats": [], "quote": "", "caption": "",
            }
        elif slide_type == "sources":
            urls = [a["url"] for a in brief.get("_articles", [])]
            content = {
                "heading": "Sources",
                "body": "",
                "bullets": urls[:6],
                "stats": [], "quote": "", "caption": "",
            }
        elif slide_type == "closing":
            content = {
                "heading": title_text or "Conclusion",
                "body": brief.get("conclusion", ""),
                "bullets": [], "stats": [],
                "quote": "", "caption": "",
            }
        else:  # content / fallback
            content = {
                "heading": title_text,
                "body": "",
                "bullets": key_points[:5],
                "stats": [], "quote": "", "caption": "",
            }

        slide_layout = _system_layout(slide_type, content, slide_index=content_idx)
        if slide_type not in ("title", "closing", "sources"):
            content_idx += 1

        gen_blocks = _content_to_blocks(content, slide_type)
        blocks = _layout_blocks(slide_type, slide_layout, gen_blocks, theme_colors, theme_fonts)
        background = _slide_background(slide_type, slide_layout, "", theme_colors)

        slide = {
            "order": i + 1,
            "type": slide_type,
            "background": background,
            "blocks": blocks,
            # Auto-populate presenter notes with citations so the user keeps
            # provenance even after editing.
            "notes": _build_notes(content, brief),
        }
        yield _sse("slide", {"index": i, "total": len(outline), "slide": slide})

        # Spawn an image task if eligible — advanced level, cap not hit, and
        # the slide type can sensibly carry a photograph.
        if (
            normalized_level == "advanced"
            and len(image_tasks) < MAX_IMAGES_PER_DECK
            and slide_type in _IMAGE_OK_TYPES
        ):
            img_prompt = _derive_image_prompt(slide_type, content)
            if img_prompt:
                task = await _spawn_image_task(i, img_prompt)
                image_tasks.append((i, task))

        # Drain completed images so the user sees them appear progressively.
        for slide_idx, task in image_tasks:
            if task.done() and not getattr(task, "_drained", False):
                task._drained = True
                url = await task
                if url:
                    yield _sse("slide_image", {"index": slide_idx, "block": _image_block(slide_idx, url)})

        await asyncio.sleep(0.02)

    # Drain any still-pending image tasks after the slide stream completes.
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
                await asyncio.sleep(0.25)

    yield _sse("complete", {"slide_count": len(outline), "token_count": token_count})


def _build_notes(content: dict, brief: dict) -> str:
    """Generate presenter notes from the content payload, citing source articles."""
    parts: list[str] = []
    if content.get("body"):
        parts.append(content["body"])
    if content.get("bullets"):
        parts.append("Talking points:\n" + "\n".join(f"• {b}" for b in content["bullets"]))
    sources = brief.get("_articles") or []
    if sources:
        cite = "Sources: " + ", ".join(f"[{s['index']}] {s['source']}" for s in sources[:5])
        parts.append(cite)
    return "\n\n".join(parts).strip()
