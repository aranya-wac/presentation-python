from __future__ import annotations

import json
from string import Template
from typing import Any

from app.ai import gemini_client
from app.models.presentation import Presentation
from app.models.template import Template as TemplateModel
from app.models.theme import Theme
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded professional sample content per template category
# ---------------------------------------------------------------------------

SAMPLE_ANALYSIS: dict[str, dict[str, Any]] = {
    "Business": {
        "title": "iNeedHelp: Finding Support, Fast.",
        "key_topics": [
            "community resources",
            "mental health support",
            "rapid connections",
            "privacy-first",
            "free access",
        ],
        "summary": (
            "iNeedHelp connects individuals to community support resources quickly, "
            "privately, and for free. We bridge the gap between people in need and "
            "the services available to them."
        ),
        "audience": "Investors and stakeholders",
        "tone": "professional",
        "estimated_slides": 10,
        "sections": [
            {
                "name": "The Problem",
                "content": "73% of people in crisis cannot find support within 24 hours. Fragmented systems leave millions behind.",
                "slide_count": 1,
            },
            {
                "name": "Our Solution",
                "content": "AI-powered matching to community resources, instant connection, privacy-first design",
                "slide_count": 1,
            },
            {
                "name": "Market Opportunity",
                "content": "$48B mental health market, 320M underserved Americans, 23% YoY growth",
                "slide_count": 1,
            },
            {
                "name": "How It Works",
                "content": "Search → Match → Connect in under 60 seconds",
                "slide_count": 1,
            },
            {
                "name": "Traction",
                "content": "12,000 users, $0 CAC, 94% satisfaction, 3 city partnerships",
                "slide_count": 1,
            },
            {
                "name": "Business Model",
                "content": "B2G SaaS licensing, city/county contracts, enterprise wellness plans",
                "slide_count": 1,
            },
            {
                "name": "The Team",
                "content": "Ex-Google, Stanford Social Impact Lab, 20+ years combined",
                "slide_count": 1,
            },
            {
                "name": "The Ask",
                "content": "Raising $2M seed to expand to 10 cities and hire engineering team",
                "slide_count": 1,
            },
        ],
    },
    "Education": {
        "title": "Future-Ready Learning: K-12 Digital Transformation",
        "key_topics": [
            "personalized learning",
            "EdTech integration",
            "student outcomes",
            "teacher enablement",
        ],
        "summary": (
            "A strategic roadmap for transforming K-12 education through adaptive "
            "technology and data-driven personalization."
        ),
        "audience": "School boards and education administrators",
        "tone": "professional",
        "estimated_slides": 10,
        "sections": [
            {
                "name": "The Challenge",
                "content": "40% of students fall behind due to one-size-fits-all teaching",
                "slide_count": 2,
            },
            {
                "name": "Our Approach",
                "content": "Adaptive AI curriculum that adjusts in real-time",
                "slide_count": 2,
            },
            {
                "name": "Outcomes",
                "content": "34% improvement in test scores, 2x teacher efficiency",
                "slide_count": 2,
            },
            {
                "name": "Implementation",
                "content": "Pilot → Scale → Sustain in 3 phases",
                "slide_count": 2,
            },
            {
                "name": "Investment",
                "content": "ROI within 18 months, federal grant eligible",
                "slide_count": 2,
            },
        ],
    },
    "Strategy": {
        "title": "Nexus Solutions: Annual Strategy Review 2025",
        "key_topics": [
            "revenue growth",
            "market expansion",
            "product innovation",
            "competitive positioning",
            "2026 outlook",
        ],
        "summary": (
            "Nexus Solutions achieved 127% YoY ARR growth in 2025. "
            "This review covers our financial highlights, strategic priorities, "
            "product roadmap, and bold targets for 2026."
        ),
        "audience": "Board of Directors and C-Suite Executives",
        "tone": "executive",
        "estimated_slides": 12,
        "sections": [
            {"name": "2025 Performance KPIs", "content": "$12.4M ARR, 45K users, 94% retention, 127% growth", "slide_count": 1},
            {"name": "Market Landscape", "content": "$42B TAM, 2.1% market share, NPS 74 vs. industry 51", "slide_count": 1},
            {"name": "Competitive Analysis", "content": "60% faster implementation, SOC 2 Type II, 120+ integrations", "slide_count": 1},
            {"name": "Financial Highlights", "content": "74% gross margin, LTV:CAC 6.2x, 28-month runway", "slide_count": 2},
            {"name": "Strategic Priorities 2026", "content": "Enterprise expansion, AI Copilot v2, EMEA/APAC, partner ecosystem", "slide_count": 2},
            {"name": "Product Roadmap", "content": "Q1 AI Copilot, Q2 Analytics Suite, Q3 Global Infra, Q4 Marketplace", "slide_count": 2},
            {"name": "2026 Targets", "content": "$28M ARR, 140 FTEs, Series B $25M, NRR 118%", "slide_count": 2},
            {"name": "Next Steps", "content": "Series B close Q1, EMEA launch Q3, Nexus Academy 10K users", "slide_count": 1},
        ],
    },
    "default": {
        "title": "Strategic Overview: Driving Results Through Innovation",
        "key_topics": [
            "strategic alignment",
            "execution excellence",
            "measurable outcomes",
            "stakeholder value",
        ],
        "summary": (
            "A comprehensive look at our strategy, execution plan, and the measurable "
            "outcomes that matter most to our stakeholders."
        ),
        "audience": "Executive leadership and stakeholders",
        "tone": "professional",
        "estimated_slides": 10,
        "sections": [
            {
                "name": "Executive Summary",
                "content": "Clear vision, focused strategy, measurable impact",
                "slide_count": 1,
            },
            {
                "name": "Current State",
                "content": "Where we are today: strengths, gaps, and opportunities",
                "slide_count": 2,
            },
            {
                "name": "Strategic Priorities",
                "content": "Three pillars: growth, efficiency, and innovation",
                "slide_count": 2,
            },
            {
                "name": "Roadmap",
                "content": "Q1–Q4 milestones and success metrics",
                "slide_count": 2,
            },
            {
                "name": "Investment & Returns",
                "content": "Resource allocation and projected ROI by quarter",
                "slide_count": 2,
            },
            {
                "name": "Next Steps",
                "content": "Immediate actions and decision points required",
                "slide_count": 1,
            },
        ],
    },
}

# Single prompt that returns ALL slides at once
_PREVIEW_BATCH_PROMPT = Template("""
You are a senior editorial designer writing GAMMA-TIER presentation copy.
Read the voice rules carefully — they are non-negotiable.

Presentation: $title
Summary: $summary
Audience: $audience

Slides to generate (in order). Each slide has a `title`, a `type`, and a
short `hint`. Use the hint as the spine; do NOT ignore it.

$outline_json

═══ GAMMA VOICE — every bullet must follow these rules ═══
1. LENGTH: 6-12 words per bullet. Hard ceiling: 14 words. Most should be 8-10.
2. PARALLEL STRUCTURE: bullets within the same slide start with the same
   part of speech (all verbs, or all nouns, or all noun-phrases).
3. SPECIFIC: use real numbers, named tools, concrete examples — never
   vague like "many", "various", "significant impact".
4. ACTIVE VOICE: subject does the action. Cut "is being", "will be done".
5. NO CORPORATE FILLER: ban "leverage", "synergize", "best-in-class",
   "next-generation", "ecosystem", "stakeholders", "going forward".
6. CONFIDENT: declarative statements, not hedged ("we believe", "may").
7. PLACEHOLDERS: tokens like [Product], [Year], [Company] MUST be replaced
   with realistic specific names that fit the deck's topic.

═══ SLIDE-TYPE RULES ═══
- title: heading ≤6 words, punchy. Subtitle bullet ≤10 words.
- agenda: heading ≤4 words. 4-6 section bullets, each 3-6 words.
- content: heading ≤5 words. 3-4 bullets, each 8-12 words, parallel.
- stats: heading ≤5 words. 3-4 stat strings like "2.4M ARR" or "73% Faster"
  (one stat per string, ALWAYS includes a number). Bullets must be empty.
- comparison: heading ≤5 words. Bullets formatted "Before: <6 words> | After: <6 words>".
- roadmap: heading ≤5 words. 3-4 phase bullets like "Phase 1: <action verb> <object>".
- quote: heading ≤6 words. quote field = 12-25 words, attributable. caption = name + role.
- closing: heading is a clear CTA verb phrase ≤6 words. One bullet ≤10 words.

═══ OUTPUT FORMAT ═══
Return a JSON array, one object per slide, same order:
[
  {
    "heading": "...",
    "body": "",
    "bullets": ["...", "..."],
    "stats": [],
    "quote": "",
    "caption": ""
  }
]

All fields required. Use empty string "" or [] when not applicable.
Respond with valid JSON array only — no markdown, no explanation.
""")


def _build_outline(analysis: dict[str, Any], target_slide_count: int | None = None) -> list[dict]:
    """Build slide outline deterministically from analysis — zero AI calls.

    If `target_slide_count` is provided, the middle (content) slot count is
    expanded/compressed so the final outline matches it exactly. This lets
    Gemini's per-section `slide_count` and the user-requested total drive the
    real shape of the deck, instead of locking it to len(sections).
    """
    sections = analysis.get("sections", [])
    title = analysis.get("title", "Presentation")
    section_names = [s["name"] for s in sections]

    outline: list[dict] = []
    order = 1

    outline.append({"order": order, "type": "title", "title": title, "key_points": []})
    order += 1

    outline.append({
        "order": order,
        "type": "agenda",
        "title": "Agenda",
        "key_points": section_names,
    })
    order += 1

    # ── Expand sections into content slots, honoring section.slide_count ──
    # Each section contributes max(1, section.slide_count) entries.
    middle_entries: list[dict] = []
    for section in sections:
        content = section.get("content", "")
        try:
            section_slides = max(1, int(section.get("slide_count", 1) or 1))
        except (TypeError, ValueError):
            section_slides = 1
        has_stats = any(c.isdigit() or c == "%" or c == "$" for c in content)
        slide_type = "stats" if has_stats and len(content) < 120 else "content"
        for _ in range(section_slides):
            middle_entries.append({
                "type": slide_type,
                "title": section["name"],
                "key_points": [content],
            })

    # If the caller requested a specific total, resize the middle to match.
    # target = title + agenda + middle + closing → middle = target - 3
    if target_slide_count is not None:
        desired_middle = max(1, target_slide_count - 3)
        if not middle_entries:
            middle_entries = [{"type": "content", "title": "Overview", "key_points": [""]}]
        if len(middle_entries) > desired_middle:
            middle_entries = middle_entries[:desired_middle]
        else:
            # Pad by duplicating the last section so Gemini's extra slides have
            # a slot. The actual slide content (and type) comes from the LLM,
            # so the duplicated outline entry is just a placeholder.
            while len(middle_entries) < desired_middle:
                last = dict(middle_entries[-1])
                middle_entries.append(last)

    for entry in middle_entries:
        outline.append({"order": order, **entry})
        order += 1

    outline.append({
        "order": order,
        "type": "closing",
        "title": "Let's Get Started",
        "key_points": [],
    })

    return outline


class PreviewGeneratorAgent:
    """
    Generates a preview Presentation using ONE AI call (vs the full pipeline's N+1).
    Outline is built deterministically; a single batch prompt returns all slide content;
    layout is rendered locally by _layout_blocks / _slide_background.
    """

    async def run(self, template: TemplateModel, theme: Theme) -> Presentation:
        from app.agents.generation.slide_generator_agent import SlideGeneratorAgent
        from app.agents.generation.template_mapper_agent import TemplateMappingResult
        from app.core.database import _session_factory

        # Gamma-tier templates ship with their own slide outline (title/type
        # per slide). Prefer that over the legacy category-based hardcoded
        # SAMPLE_ANALYSIS — it gives us template-specific, accurate previews
        # instead of one-size-fits-all "Strategic Overview" placeholder text.
        template_slides = template.slides or []
        has_template_outline = (
            bool(template_slides)
            and isinstance(template_slides[0], dict)
            and "type" in template_slides[0]
            and "title" in template_slides[0]
            and "blocks" not in template_slides[0]  # legacy already-rendered slides
        )

        meta = template.metadata_json or {}

        if has_template_outline:
            # Build outline directly from the template's authored slide list.
            outline = [
                {
                    "order": s.get("order", i + 1),
                    "type":  s.get("type", "content"),
                    "title": s.get("title", ""),
                    "key_points": s.get("body", []) if isinstance(s.get("body"), list) else [s.get("body", "")],
                }
                for i, s in enumerate(template_slides)
            ]
            analysis = {
                "title": template_slides[0].get("title", template.name) if template_slides else template.name,
                "summary": template.description or "",
                "audience": meta.get("audience", "General audience"),
                "tone": meta.get("tone", "professional"),
                "estimated_slides": len(outline),
                "sections": [],
            }
            logger.info(
                f"Preview using template outline for '{template.name}' "
                f"({len(outline)} slides) — single AI call"
            )
        else:
            # Fallback to legacy category-based sample (kept for templates
            # that don't ship their own outline).
            category = template.category or "default"
            analysis = dict(SAMPLE_ANALYSIS.get(category, SAMPLE_ANALYSIS["default"]))
            total_slides = meta.get("total_slides", analysis["estimated_slides"])
            analysis["estimated_slides"] = total_slides
            outline = _build_outline(analysis)
            logger.info(
                f"Preview using category sample for '{template.name}' "
                f"(category={category}, slides={total_slides}) — single AI call"
            )

        # ONE AI call to flesh out the per-slide content from the outline.
        prompt = _PREVIEW_BATCH_PROMPT.substitute(
            title=analysis["title"],
            summary=analysis["summary"],
            audience=analysis["audience"],
            outline_json=json.dumps(
                [
                    {
                        "order": s["order"],
                        "type": s["type"],
                        "title": s["title"],
                        "hint": " · ".join(s.get("key_points", []))[:200] if s.get("key_points") else "",
                    }
                    for s in outline
                ],
                indent=2,
            ),
        )
        contents: list[dict] = await gemini_client.generate_json(prompt)

        # Pad/trim to match outline length
        while len(contents) < len(outline):
            contents.append({"heading": "", "body": "", "bullets": [], "stats": [], "quote": "", "caption": ""})
        contents = contents[: len(outline)]

        # 3. Build mapping (no AI — just wraps template+theme)
        mapping = TemplateMappingResult(template=template, theme=theme)

        # 4. Render slides locally using existing layout engine
        agent = SlideGeneratorAgent()
        slides = await agent._build_slides(outline, contents, mapping, logo_url="")

        # 5. Generate Gamma-tier AI illustrations for MULTIPLE slides — title,
        # closing, and content slides — all in PARALLEL. Each illustration
        # uses the theme's `illustration_mood` token so the deck has visual
        # consistency. Slides with their own visuals (chart/roadmap/stats/
        # comparison/kanban/funnel) skip — they already carry imagery.
        await self._attach_illustrations(slides, contents, outline, theme, template)

        # 5. Persist as preview
        async with _session_factory() as db:
            presentation = Presentation(
                user_id=None,
                template_id=template.id,
                theme_id=theme.id,
                title=f"{template.name} — Preview",
                description=f"Auto-generated preview for template: {template.name}",
                logo_url="",
                slides=slides,
                is_preview=True,
            )
            db.add(presentation)
            await db.commit()
            await db.refresh(presentation)

        logger.info(f"Preview presentation created: {presentation.id}")
        return presentation

    # ── Layout detection & image placement ────────────────────────────────
    @staticmethod
    def _detect_layout(slide: dict) -> str:
        """Detect what layout was applied to this slide by inspecting its blocks.
        Returns one of: 'title_hero', 'closing', 'split_panel', 'card_grid',
        'content_clean', 'agenda_rows', 'structured', 'unknown'."""
        blocks = slide.get("blocks", [])
        ids = {b.get("id", "") for b in blocks}
        stype = slide.get("type", "")
        # Layouts with built-in visual structure — leave alone.
        if stype in ("stats", "chart", "quote", "comparison", "kanban", "funnel", "roadmap"):
            return "structured"
        if stype in ("title",) or "hero-eyebrow" in ids:
            return "title_hero"
        if stype == "closing" or "cta" in ids:
            return "closing"
        if "left-panel" in ids:
            return "split_panel"
        if any(bid.startswith("agenda-row-") for bid in ids):
            return "agenda_rows"
        if any(bid.startswith("card-") for bid in ids):
            return "card_grid"
        return "content_clean"

    @staticmethod
    def _image_strategy_for_layout(layout: str) -> dict | None:
        """Return how/where to place an AI illustration for a given layout.
        None means skip (the layout has no room or already has visuals).

        Returns dict with: position (x,y,w,h) + action ('replace_left_panel' |
        'add_left' | 'add_right')."""
        if layout == "title_hero":
            # FULL-BLEED background photograph behind the entire title slide.
            # This same image is re-used as a deck-wide backdrop on every
            # other slide (see _attach_illustrations). Matches Gamma's
            # "Resource Allocation Plan" / "Industry Benchmark" style.
            return {"action": "full_bleed_bg", "x": 0, "y": 0, "w": 1280, "h": 720}
        if layout == "closing":
            return {"action": "full_bleed_bg", "x": 0, "y": 0, "w": 1280, "h": 720}
        # No per-slide illustrations — Gamma's reference decks don't decorate
        # individual slides with small AI illustrations. The deck-wide
        # atmospheric backdrop alone provides visual continuity. Per-slide
        # layout structure (cards, agenda, comparison) carries the content.
        if layout == "content_clean":
            # Skip — content_clean uses full-width heading + bullets and
            # forcing them into the right half creates awkward narrow text.
            # If we want images here later, redesign the content_clean layout.
            return None
        # agenda, card_grid, structured: SKIP — they already use the full width.
        return None

    # ── Multi-slide illustration generation ────────────────────────────────
    @staticmethod
    async def _attach_illustrations(
        slides: list[dict],
        contents: list[dict],
        outline: list[dict],
        theme,
        template,
    ) -> None:
        """Generate AI illustrations for title/closing/content slides in
        parallel and attach them to each slide as image blocks.

        Skips slides that already carry visual structure (chart, roadmap,
        stats, comparison, kanban, funnel) — those slide types are visual
        on their own and don't need an illustration.
        """
        import asyncio
        import uuid
        from app.ai.gemini_client import generate_image
        from app.api.v1.images import GENERATED_DIR

        if not slides:
            return

        theme_tokens = (theme.fonts or {}).get("_tokens", {})
        illustration_mood = theme_tokens.get(
            "illustration_mood",
            "deep blues and indigo, modern minimal abstract",
        )
        theme_bg = (theme.colors or {}).get("background", "#09090B")

        # Detect theme luminance so the AI-generated backdrop matches the
        # theme's brightness — a dark theme gets a dark moody texture, a
        # light cream theme gets a light airy texture. Otherwise the dark
        # heading text on a light theme becomes invisible against a forced
        # dark bg.
        def _is_dark(hex_color: str) -> bool:
            h = (hex_color or "").lstrip("#")
            if len(h) != 6:
                return True
            try:
                r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)
            except ValueError:
                return True
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 < 0.5

        theme_is_dark = _is_dark(theme_bg)
        if theme_is_dark:
            mood_descriptor = "very dark and moody, deep shadows"
            text_overlay_note = (
                "The image must function as a BACKDROP for WHITE overlay text — "
                "overall darkness 7/10, no bright focal point in the center."
            )
        else:
            mood_descriptor = "soft, warm, light and airy, cream/paper toned, gentle"
            text_overlay_note = (
                "The image must function as a BACKDROP for DARK overlay text — "
                "overall brightness 7/10, no dark focal point in the center, "
                "soft pale tones throughout."
            )

        # Cap total images per preview. Lower = more selective (Gamma-tier
        # decks don't illustrate every slide — only key ones).
        MAX_ILLUSTRATIONS = 4

        # Decide which slides get illustrations + position for each.
        # Layout-aware: only place an image where the existing layout has
        # room for it. SKIP layouts that already use the full width.
        targets: list[tuple[int, str, dict, str, str]] = []  # +slide_type
        for i, slide in enumerate(slides):
            layout = PreviewGeneratorAgent._detect_layout(slide)
            strategy = PreviewGeneratorAgent._image_strategy_for_layout(layout)
            if strategy is None:
                continue
            heading = contents[i].get("heading") if i < len(contents) else slide.get("type", "")
            subject = heading or slide.get("type", "presentation")
            pos = {"x": strategy["x"], "y": strategy["y"], "w": strategy["w"], "h": strategy["h"]}
            targets.append((i, subject, pos, strategy["action"], layout))
            if len(targets) >= MAX_ILLUSTRATIONS:
                break

        if not targets:
            return

        async def _gen_one(idx: int, subject: str, pos: dict, action: str, layout: str) -> tuple[int, str | None, dict, str]:
            try:
                # Title/closing slides → FULL-BLEED dark atmospheric texture
                # photograph that fills the whole slide as a background, with
                # text overlaying on top. Matches Gamma's "Industry Benchmark
                # Overview" title style: layered organic forms, wavy textures,
                # architectural sand-dune-like surfaces, moody and dark.
                if layout in ("title_hero", "closing"):
                    prompt = (
                        f"Abstract atmospheric photograph for a presentation title-slide BACKGROUND. "
                        f"Subject: organic flowing textured surfaces — choose ONE: layered curved sand "
                        f"dunes; smooth wave-like architectural folds; stone or marble with subtle "
                        f"ridges; flowing fabric folds; abstract topographic ripples. "
                        f"PALETTE: dominantly {theme_bg} tones, {mood_descriptor}, subtle highlights "
                        f"of {illustration_mood}. Lighting: low contrast, soft directional grazing light "
                        f"revealing texture. Mood: refined, minimal, editorial. "
                        f"{text_overlay_note} "
                        f"NO people, NO faces, NO illustrations, NO buildings, NO cities, NO logos, "
                        f"NO text, NO words, NO charts, NO icons, NO neon, NO futuristic, NO sci-fi, "
                        f"NO oversaturated colors. Premium magazine photography quality, like Gamma's "
                        f"flagship editorial title decks. 16:9 landscape aspect ratio, full-bleed, "
                        f"edge-to-edge."
                    )
                else:
                    # Per-slide content illustration. Must visually harmonize
                    # with the deck-wide atmospheric backdrop (dark wavy
                    # texture) — same photographic-texture language, just a
                    # different focal subject for variety. NO flat vector
                    # illustrations, NO bright color blocks.
                    subject_l = subject.lower()
                    if any(k in subject_l for k in ("compare", "vs", "before", "after", "matrix", "tradeoff")):
                        texture_hint = "intersecting geometric stone planes meeting at a soft seam, low-light architectural texture"
                    elif any(k in subject_l for k in ("ecosystem", "network", "framework", "pillar", "system", "architecture", "platform")):
                        texture_hint = "interlocking organic layered forms, like nested architectural folds or concentric ridges"
                    elif any(k in subject_l for k in ("process", "step", "phase", "workflow", "journey", "pipeline", "timeline", "roadmap")):
                        texture_hint = "long flowing curved lines of layered sand or fabric, suggesting movement and direction"
                    elif any(k in subject_l for k in ("growth", "trend", "metric", "data", "performance", "result", "analytics")):
                        texture_hint = "rising topographic ridges with grazing light, abstract elevation contour lines"
                    else:
                        texture_hint = "smooth organic textured surface — wavy fabric folds, dark marble veining, or layered stone"

                    prompt = (
                        f"Abstract atmospheric photograph for a presentation slide visual. "
                        f"Subject: {texture_hint}. "
                        f"PALETTE: dominantly {theme_bg} tones, {mood_descriptor}, subtle highlights of "
                        f"{illustration_mood}. Lighting: soft directional grazing light revealing texture. "
                        f"Mood: refined, minimal, editorial — same visual language as the deck's "
                        f"atmospheric backdrop. "
                        f"NO people, NO faces, NO illustrations, NO flat vector art, NO oversaturated colors, "
                        f"NO buildings, NO cities, NO charts, NO icons, NO neon, NO futuristic, NO sci-fi, "
                        f"NO logos, NO text, NO words, NO gray frame, NO border. "
                        f"1:1 SQUARE aspect ratio, full-bleed. The image must blend seamlessly with a "
                        f"{theme_bg} atmospheric slide — corner pixels must match {theme_bg} closely. "
                        f"Premium magazine photography quality."
                    )
                img_bytes, mime = await asyncio.wait_for(generate_image(prompt), timeout=30.0)
                GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                ext = mime.split("/")[-1] if "/" in mime else "png"
                if ext == "jpeg":
                    ext = "jpg"
                fname = f"{uuid.uuid4()}.{ext}"
                (GENERATED_DIR / fname).write_bytes(img_bytes)
                return (idx, f"/generated/{fname}", pos, action)
            except Exception as exc:
                logger.warning(f"Illustration gen failed for slide {idx} ({subject}): {exc}")
                return (idx, None, pos, action)

        logger.info(
            f"Generating {len(targets)} illustrations in parallel for '{template.name}'"
        )
        # Limit concurrency — Nano Banana on a paid plan handles a few in
        # parallel but a burst of 6+ can hit rate limits. Semaphore = 3.
        sem = asyncio.Semaphore(3)

        async def _bounded(idx, subj, p, a, lay):
            async with sem:
                return await _gen_one(idx, subj, p, a, lay)

        results = await asyncio.gather(*[_bounded(i, s, p, a, lay) for (i, s, p, a, lay) in targets])

        # Capture the title's full-bleed atmospheric background so we can
        # re-use it as a deck-wide backdrop on every other slide. Gives the
        # whole deck a consistent Gamma-style atmospheric feel.
        deck_bg_url: str | None = None

        for (slide_idx, url, pos, action) in results:
            if not url:
                continue
            slide = slides[slide_idx]
            slide.setdefault("blocks", [])

            if action == "replace_left_panel":
                # Remove the existing decorative left panel + accent dot so
                # the AI image sits cleanly in the left half of split_panel.
                slide["blocks"] = [
                    b for b in slide["blocks"]
                    if b.get("id") not in ("left-panel", "panel-dot")
                ]

            image_block = {
                "id": f"preview-illust-{slide_idx}",
                "type": "image",
                "content": url,
                "position": pos,
                "styling": {
                    "font_family": "", "font_size": 0, "font_weight": 0,
                    "color": "transparent", "background_color": "transparent",
                    "text_align": "left",
                },
            }
            if action == "full_bleed_bg":
                # Full-bleed background — image must render BEHIND all other
                # blocks (title text, eyebrow, etc.), so insert at index 0.
                slide["blocks"].insert(0, image_block)
                # First full-bleed image becomes the deck-wide backdrop.
                if deck_bg_url is None:
                    deck_bg_url = url
            else:
                slide["blocks"].append(image_block)

        # Apply the deck-wide atmospheric backdrop to every slide that doesn't
        # already have a full-bleed image. The block id `deck-bg-*` tells the
        # frontend to render a stronger dark overlay so cards/text stay
        # readable on content slides. Also strip the split_panel decorative
        # left-panel block since the wavy backdrop now covers that region.
        if deck_bg_url:
            for i, slide in enumerate(slides):
                slide.setdefault("blocks", [])
                blocks = slide["blocks"]
                has_full_bleed = any(
                    b.get("type") == "image"
                    and b.get("position", {}).get("x") == 0
                    and b.get("position", {}).get("y") == 0
                    and b.get("position", {}).get("w", 0) >= 1200
                    for b in blocks
                )
                if has_full_bleed:
                    continue
                # Remove decorative left-panel artifacts — the deck-wide bg
                # replaces them visually and they would just sit on top.
                slide["blocks"] = [
                    b for b in blocks
                    if b.get("id") not in ("left-panel", "panel-dot")
                ]
                slide["blocks"].insert(0, {
                    "id": f"deck-bg-{i}",
                    "type": "image",
                    "content": deck_bg_url,
                    "position": {"x": 0, "y": 0, "w": 1280, "h": 720},
                    "styling": {
                        "font_family": "", "font_size": 0, "font_weight": 0,
                        "color": "transparent", "background_color": "transparent",
                        "text_align": "left",
                    },
                })
