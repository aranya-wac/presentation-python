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
You are a presentation content writer. Generate content for ALL slides in one response.

Presentation: $title
Summary: $summary
Audience: $audience

Slides to generate (in order):
$outline_json

For each slide return content matching its type:
- title: heading (≤8 words) + one subtitle bullet
- agenda: heading + 4-6 section name bullets
- content: heading (≤6 words) + 3-5 concrete bullet points
- stats: heading + 3-4 stat strings like "47% Cost Reduction"
- closing: call-to-action heading + one next-step bullet

Return a JSON array with exactly one object per slide, same order:
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

        category = template.category or "default"
        analysis = dict(SAMPLE_ANALYSIS.get(category, SAMPLE_ANALYSIS["default"]))

        meta = template.metadata_json or {}
        total_slides = meta.get("total_slides", analysis["estimated_slides"])
        analysis["estimated_slides"] = total_slides

        logger.info(
            f"Generating preview for '{template.name}' "
            f"(category={category}, slides={total_slides}) — single AI call"
        )

        # 1. Build outline with zero AI calls
        outline = _build_outline(analysis)

        # 2. ONE AI call for all slide content
        prompt = _PREVIEW_BATCH_PROMPT.substitute(
            title=analysis["title"],
            summary=analysis["summary"],
            audience=analysis["audience"],
            outline_json=json.dumps(
                [{"order": s["order"], "type": s["type"], "title": s["title"]} for s in outline],
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
        slides = agent._build_slides(outline, contents, mapping, logo_url="")

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
