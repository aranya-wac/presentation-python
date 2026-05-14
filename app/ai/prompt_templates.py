from __future__ import annotations

from string import Template

CONTENT_ANALYSIS_PROMPT = Template("""
You are an expert presentation content analyst. Analyze the following document content and extract structured information.

Document content:
$content

Return a JSON object with this exact structure:
{
  "title": "Suggested presentation title",
  "key_topics": ["topic1", "topic2", ...],
  "summary": "Brief 2-3 sentence summary of the content",
  "audience": "Target audience description",
  "tone": "professional|casual|technical|inspirational",
  "estimated_slides": <number between 8 and 15>,
  "sections": [
    {"name": "section name", "content": "key points from this section", "slide_count": <number>}
  ]
}

Respond with valid JSON only, no markdown or extra text.
""")

OUTLINE_GENERATION_PROMPT = Template("""
You are an expert presentation strategist. Your job is to create a concise, structured outline for a presentation based on the document analysis provided. Do NOT write full content — only identify the slides needed and their key points.

Document Analysis:
$analysis

Generate an outline for a presentation with $slide_count slides.

Rules:
- First slide must be type "title"
- Last slide must be type "closing"
- Include at least one "agenda" slide near the start
- Use types: title | agenda | content | quote | stats | closing
- key_points must be 2-3 short phrases (not full sentences) describing what this slide will cover
- All content must be grounded in the source document — no invented topics
- Each slide must cover a DISTINCT angle or sub-topic — key_points must NOT overlap or repeat across slides
- Slide titles must be unique; if topics are closely related, split them by depth (definition → process → examples → comparison) rather than restating the same idea

Return a JSON array with exactly $slide_count items:
[
  {
    "order": 1,
    "type": "title|agenda|content|quote|stats|closing",
    "title": "Short slide title (≤8 words)",
    "key_points": ["key point 1", "key point 2", "key point 3"]
  }
]

Respond with valid JSON array only — no markdown fences, no explanation.
""")

SLIDE_CONTENT_PROMPT = Template("""
You are an expert presentation content writer. Generate the full content for ONE slide based on the outline item and document context provided. Do NOT decide layout, colors, fonts, or visuals — the system handles all of that.

Slide Outline Item (THIS slide):
$outline_item

Full Presentation Outline (ALL slides — for context, do NOT generate content for these):
$full_outline

Full Document Context:
$analysis_summary

Slide type: $slide_type

Write content appropriate for this slide type:
- "title": strong headline (≤8 words) as heading + punchy tagline (≤15 words) as a subtitle bullet
- "agenda": heading + 4-6 section names as bullets
- "content": heading (≤6 words) + 3-5 bullets (10-20 words each, concrete and specific) + optional body paragraph
- "quote": one memorable quote (20-40 words) as quote + attribution as caption
- "stats": heading + 3-4 data points as stats in format "VALUE Label" (e.g. "47% Cost Reduction")
- "closing": call-to-action headline as heading + contact/next-step as subtitle bullet

Return a single JSON object (NOT an array):
{
  "heading": "Slide heading text",
  "body": "Optional body paragraph text (for content slides), empty string otherwise",
  "bullets": ["bullet point 1", "bullet point 2"],
  "stats": ["47% Cost Reduction", "$2.4B Market Size"],
  "quote": "Quote text if this is a quote slide, empty string otherwise",
  "caption": "Attribution or caption text, empty string if not applicable"
}

Rules:
- All fields are always present; use empty string "" or empty array [] when not applicable
- "stats" array only for stats slides; "quote" only for quote slides
- All content must come from the source document — no placeholders, no invented facts
- Restrict yourself STRICTLY to THIS slide's key_points. Do NOT repeat bullets, examples, or framings already assigned to other slides in the full outline above
- If THIS slide's topic overlaps another, focus on the unique angle implied by THIS slide's title and key_points only
- Respond with valid JSON object only — no markdown fences, no explanation
""")

PREVIEW_GENERATION_PROMPT = Template("""
You are a presentation content creator. Generate a complete sample presentation for the template "$template_name".

Template category: $category
Target audience: $audience
Theme: $theme_name

Generate a realistic sample presentation with $slide_count slides demonstrating this template's structure.

Return a JSON array of slides:
[
  {
    "order": 1,
    "type": "title",
    "blocks": [
      {
        "id": "unique-block-id",
        "type": "text|image|title|subtitle|bullet",
        "content": "sample content here"
      }
    ]
  }
]

Make the content realistic, professional, and relevant to the template's purpose.
Respond with valid JSON array only.
""")


OUTLINE_ONLY_PROMPT = Template("""
You are planning a presentation. Produce ONLY an outline — slide titles and types — not slide content.
This is fast and lets the user review the structure before paying for full slide generation.

User prompt: $prompt

Document content:
$content

Required slide count: $slide_count
Presentation level: $level

Return ONLY this JSON (no markdown, no explanation):
{
  "title": "Deck title (≤10 words)",
  "summary": "2 sentences",
  "audience": "Target audience",
  "tone": "professional",
  "sections": [
    {"name": "Section name", "content": "1-line description", "slide_count": 2}
  ],
  "slides": [
    {
      "order": 1,
      "type": "title|agenda|content|stats|quote|chart|roadmap|closing",
      "title": "Slide title (≤8 words)"
    }
  ]
}

Rules:
- slides array MUST have exactly $slide_count items
- slides[0].type = "title"; slides[1].type = "agenda"; slides[-1].type = "closing"
- For middle slides, pick the type that best fits each slide's intended content
- Only suggest "chart" type when the source contains comparable numbers; "roadmap" when there's a phased/sequenced topic; "stats" when there are standalone metrics; "quote" when the source has a quotation
- DO NOT produce bullets, body, stats values, chart data, or notes — just title + type
- All slide topics must come from the source — no invented topics
""")


COMBINED_GENERATION_PROMPT = Template("""
Generate a presentation deck as JSON.

User prompt: $prompt

Source content:
$content

Slide count: exactly $slide_count
Level: $level
$level_instructions

Slide types: title | agenda | content | stats | quote | chart | roadmap | comparison | kanban | funnel | closing
- title=opener, agenda=section list, closing=final. Middle slides pick the best fit.
- CRITICAL: structured types (stats/chart/roadmap/comparison/kanban/funnel/quote) require REAL supporting data. If you pick `comparison` you MUST fill BOTH left.items AND right.items with 3+ items each. If you pick `chart` you MUST fill chart.data with 3+ numeric (label, value) pairs. If you pick `kanban` you MUST fill all 3 columns with items. If you pick `funnel` you MUST fill 3+ stages with labels and values. If you pick `roadmap` you MUST fill 3+ {phase, label} items. If you pick `stats` you MUST fill 2+ items.
- NEVER pick a structured type and leave its supporting field empty — that produces a broken slide. If you can't fill it from the source, use `content` instead.
- Never invent facts or numbers. If the source doesn't support a structured type, use content.

Return ONLY this JSON (no markdown):
{
  "title": "≤10 words",
  "summary": "2 sentences",
  "audience": "...",
  "tone": "professional",
  "sections": [{"name": "...", "content": "...", "slide_count": 1}],
  "slides": [
    {
      "type": "content",
      "heading": "≤8 words",
      "body": "",
      "bullets": ["≤14 words each"],
      "stats": ["47% Cost Reduction"],
      "quote": "",
      "caption": "",
      "chart": {"type": "bar|line|pie", "data": [{"label": "Q1", "value": 12}]},
      "roadmap": [{"phase": "Q1 2026", "label": "≤8 words"}],
      "comparison": {"left": {"label": "Before", "items": ["..."]}, "right": {"label": "After", "items": ["..."]}},
      "columns": [{"label": "Problem", "items": ["..."]}, {"label": "Solution", "items": ["..."]}, {"label": "Result", "items": ["..."]}],
      "funnel": [{"label": "Visitors", "value": "10,000"}],
      "image_prompt": "",
      "notes": "2–3 sentences, ≤80 words, natural spoken language"
    }
  ]
}

Constraints:
- slides[] length = $slide_count. slides[0].type="title". slides[1].type="agenda" (bullets = section names). slides[-1].type="closing".
- bullets ≤6 items, ≤14 words each. Empty for title/closing/chart/roadmap/comparison/kanban/funnel.
- stats: 2–4 items only on type=stats. Format "<NUMBER><UNIT> <Label>".
- chart.data: 3–8 (label, numeric value) pairs, only on type=chart.
- roadmap: 3–6 {phase, label} items, only on type=roadmap.
- comparison: 3–5 items per side, only on type=comparison.
- columns: exactly 3 columns, 2–4 items each, only on type=kanban.
- funnel: 3–5 stages, only on type=funnel.
- image_prompt: optional, ≤5 per deck (only honoured at advanced level — see level instructions), one short sentence describing a concrete photographable subject. "" otherwise.
- STRICT: leave EVERY structured field EMPTY ({} or []) on slides where its type doesn't match. A "kanban" slide must have `comparison: {}` and `funnel: []` and `chart: {}` and `roadmap: []`. A "chart" slide must have `comparison: {}`, `columns: []`, `funnel: []`, `roadmap: []`. Filling structured fields on the wrong slide type renders broken panels.
- All fields always present. Use "", [], or {} when empty.
""")


RESEARCH_SUMMARY_PROMPT = Template("""
You are a research analyst synthesizing recent news articles into structured insights for a presentation.

Topic: $topic
Audience: $audience
Style: $style

Articles (numbered, with source domain and full text):
$articles

Your job is to produce a JSON research brief. STRICT rules:
- Use ONLY facts present in the articles. If something isn't there, omit it. NEVER invent statistics, dates, or quotes.
- When citing a fact, append article numbers in brackets, e.g. "Floods displaced 2.4M people [1][3]".
- If articles disagree, surface the disagreement explicitly in `risks` or `key_points` (e.g. "Sources differ on casualty count: [1] reports 12, [4] reports 30").
- If a section has no supporting material, return an empty array/string for it — do not pad.

Return ONLY this JSON (no markdown fences, no commentary):
{
  "title": "Concise presentation title (≤10 words)",
  "overview": "2–3 sentence neutral summary of the topic.",
  "key_points": ["fact 1 [n]", "fact 2 [n]", "..."],
  "timeline": [
    {"date": "ISO or human date", "event": "what happened [n]"}
  ],
  "statistics": [
    {"value": "exact number from article", "label": "what it measures", "source_index": 1}
  ],
  "trends": ["trend 1 [n]", "trend 2 [n]"],
  "risks": ["risk or open question [n]"],
  "conclusion": "1–2 sentence forward-looking takeaway, grounded in the articles.",
  "sources_used": [1, 2, 4]
}
""")


TOPIC_OUTLINE_PROMPT = Template("""
You are planning the slide order for a presentation based on a research brief.

Topic: $topic
Audience: $audience
Style: $style
Slide count target: $slide_count

Research brief (JSON):
$brief

Return ONLY a JSON array of slide intents in display order. Each item:
{"order": 1, "type": "title|agenda|content|stats|timeline|quote|closing|sources",
 "title": "slide title (≤8 words)",
 "key_points": ["talking point 1", "talking point 2"]}

Rules:
- First slide must be type "title".
- Second slide must be type "agenda" listing the major sections.
- Last slide must be type "sources" listing the article URLs that informed the deck.
- Use type "stats" for slides primarily about numeric data (values from research.statistics).
- Use type "timeline" for chronological slides if research.timeline has entries.
- Use type "content" for narrative sections.
- Length: exactly $slide_count items. Pad or condense as needed but never invent material.
""")


def render(template: Template, **kwargs) -> str:
    return template.substitute(**kwargs)


# Level-specific guidance injected into COMBINED_GENERATION_PROMPT.
# Maps to slide types the renderer supports: title, agenda, content, stats, quote, closing.
LEVEL_INSTRUCTIONS_SIMPLE = """\
Style: minimal, clean, professional. The audience scans, doesn't read.
- Use ONLY these slide types: title, agenda, content, closing. At most 1 "stats" slide if the source has real numbers.
- Do NOT use "chart", "roadmap", "comparison", "kanban", "funnel", "quote", or "image_prompt" — keep image_prompt as "".
- "content" slides use 3–5 short bullets (≤8 words per bullet).
- Never invent data. No marketing fluff, no hype.
- Leave the "caption" field empty unless a one-line clarifier genuinely helps.
"""

LEVEL_INSTRUCTIONS_ADVANCED = """\
Style: rich, data-driven, executive-grade. Treat the deck like a board briefing — Gamma-quality visual variety.
- All slide types available: content, stats, quote, chart, roadmap, comparison, kanban, funnel. Pick whichever genuinely fits — DO NOT default everything to content.
- AIM for visual variety: in a 10-slide deck, target ~2 stats slides, 1 chart, 1 roadmap or comparison or kanban or funnel, 1 quote (if source has one), rest content. Adjust if source doesn't support a type.
- "stats" slides: 2–4 standalone metrics ("47% Cost Reduction", "$2.4M ARR").
- "chart": fill chart.data with real (label, numeric value) pairs. Pick chart.type: bar=comparisons, line=time series, pie=parts-of-whole.
- "roadmap": 3–6 phases/milestones with concrete labels.
- "comparison": two sides with 3–5 items each — before/after, manual/automated, us/them.
- "kanban": exactly 3 columns — problem/solution/result, before/now/after.
- "funnel": 3–5 stages with real numbers showing drop-off.
- "quote": only when source has an actual quotation.
- "image_prompt": Use on the TITLE slide (mandatory hero image) and on 2–4 more slides that benefit from visual support (people/places/products/concepts/team). Up to 5 image_prompts total per deck. One short sentence each. Leave "" on data-heavy slides (stats/chart/comparison/kanban/funnel — the data IS the visual).
- Content bullets up to ~16 words; carry insight, not labels.
- Notes per slide: 2–3 sentences a presenter would actually say, ≤80 words.
- Never invent facts. If a structured type can't be backed by source data, fall back to content.
"""


def level_instructions(level: str) -> str:
    """Return guidance block for the given deck level."""
    return LEVEL_INSTRUCTIONS_ADVANCED if (level or "").lower() == "advanced" else LEVEL_INSTRUCTIONS_SIMPLE
