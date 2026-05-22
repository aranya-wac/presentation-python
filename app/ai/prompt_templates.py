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

═══ GAMMA-TIER VOICE (NON-NEGOTIABLE) ═══
1. Bullets are 6-12 words each. Hard ceiling 14. Most should be 8-10.
2. Parallel structure within a slide — all bullets start with the same part of speech.
3. Specific: real numbers, named tools, concrete examples. Never "many", "various", "significant".
4. Active voice. No "is being", "will be done by".
5. Banned filler: "leverage", "synergize", "best-in-class", "next-generation",
   "ecosystem", "stakeholders", "going forward", "robust", "seamless", "innovative".
6. Confident declarative. No "we believe", "may", "could potentially".

Write content appropriate for this slide type:
- "title": punchy headline (≤6 words) + tagline subtitle (≤10 words)
- "agenda": heading (≤4 words) + 4-6 section names (3-6 words each)
- "content": heading (≤5 words) + 3-4 bullets (8-12 words each, parallel structure, specific)
- "quote": one memorable quote (12-25 words) + named attribution (name + role)
- "stats": heading (≤5 words) + 3-4 data points like "73% Faster Resolution" (each MUST include a number)
- "closing": CTA verb-phrase heading (≤6 words) + next-step bullet (≤10 words)

Return a single JSON object (NOT an array):
{
  "heading": "Slide heading text",
  "body": "Optional body paragraph text (for content slides), empty string otherwise",
  "bullets": ["bullet point 1", "bullet point 2"],
  "stats": ["47% Cost Reduction", "$$2.4B Market Size"],
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
- slides[0].type = "title"; slides[-1].type = "closing"; include slides[1].type = "agenda" only when slide_count >= 8
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

SOURCE HANDLING:
- If the source content above is a real document (substantial long-form text),
  ground every fact strictly in it and do not invent.
- If the source is only a short topic or instruction (no real document), expand it
  into a full, substantive deck using your own expert knowledge of the subject.
  Supply realistic, representative facts, figures, and examples a domain expert
  would actually cite. A thin or vague deck is a failure.

Slide types: title | agenda | content | stats | quote | chart | roadmap | comparison | kanban | funnel | closing
- title=opener, closing=final. agenda=section list — only worth a slide on decks of 8+ slides. Every middle slide picks the best fit and carries real content.
- CRITICAL: structured types (stats/chart/roadmap/comparison/kanban/funnel/quote) require REAL supporting data. If you pick `comparison` you MUST fill BOTH left.items AND right.items with 3+ items each. If you pick `chart` you MUST fill chart.data with 3+ numeric (label, value) pairs. If you pick `kanban` you MUST fill all 3 columns with items. If you pick `funnel` you MUST fill 3+ stages with labels and values. If you pick `roadmap` you MUST fill 3+ {phase, label} items. If you pick `stats` you MUST fill 2+ items.
- NEVER pick a structured type and leave its supporting field empty — that produces a broken slide. If you can't fill it from the source, use `content` instead.
- With a real source document, never invent facts or numbers — if it doesn't support a structured type, use content. With a topic-only prompt, write accurate, representative domain knowledge.

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
      "bullets": ["Label: one specific, concrete sentence — 12-22 words"],
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

═══ GAMMA-TIER VOICE (NON-NEGOTIABLE) ═══
Every bullet you write must follow these rules. Treat them as hard constraints:
1. LENGTH: agenda and closing bullets stay short (3-8 words). Content bullets carry
   a label plus one sentence — 12-22 words (see CONTENT DEPTH below).
2. PARALLEL STRUCTURE: bullets within the same slide start with the same
   part of speech (all verbs, or all noun-phrases). Pick one and stick.
3. SPECIFIC: use real numbers, named tools, concrete examples — never
   "many", "various", "significant", "robust", "world-class".
4. ACTIVE VOICE: subject does the action. Cut "is being", "will be done by".
5. NO CORPORATE FILLER: ban "leverage", "synergize", "best-in-class",
   "next-generation", "ecosystem", "stakeholders", "going forward",
   "robust", "seamless", "innovative", "cutting-edge".
6. CONFIDENT DECLARATIVE: state facts. No "we believe", "may", "could potentially".
7. FULL SLIDES: content slides carry 4 substantive bullets (drop to 3 only when the topic genuinely offers no more). Never ship a thin, half-empty slide.

═══ CONTENT DEPTH — make every slide full and informative ═══
- This deck must read as complete and expert-written, never a sparse outline.
- Each "content" slide: 4 bullets (3 only if the topic truly has no more).
- Format every content bullet as "Label: explanation" — a 2-4 word label, a colon,
  then ONE concrete sentence that teaches something specific. 12-22 words total.
  Example: "Habitat range: Snow leopards patrol territories spanning up to 1,000 km of rugged terrain."
- Fill the "body" field of content slides with a 1-2 sentence framing line
  (≤32 words) that sets up the bullets.
- Favor real specifics — named examples, realistic figures, concrete mechanisms —
  over generic statements.

═══ STRUCTURAL CONSTRAINTS ═══
- slides[] length = $slide_count. slides[0].type="title". slides[-1].type="closing".
- AGENDA IS OPTIONAL: include ONE "agenda" slide as slides[1] ONLY when $slide_count is 8 or more. For decks under 8 slides, SKIP the agenda entirely — go straight from the title into content slides so no slide is wasted on scaffolding.
- Every slide between the title and the closing MUST carry real, specific topic content. Never produce a filler or structural slide.
- Each slide must cover a DISTINCT angle of the topic. NEVER repeat the same numbers, claims, or framing across slides. If two slides would land on the same idea, change one to a different facet (e.g. temporal: 2024 vs 2028; geographic: APAC vs EMEA; functional: technology stack vs adoption metrics; stakeholder: customers vs investors vs employees). When the user prompt is short or vague, generate slides that each take an obviously different sub-topic — do not pad with restatements.
- bullets: 3-4 items on content slides, ≤22 words each. Empty for title/closing/chart/roadmap/comparison/kanban/funnel.
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


FLOW_GENERATION_PROMPT = Template("""
You are an expert presentation designer building a deck in the GAMMA style:
flexible-height cards, each a DISTINCT composition of content blocks.

User prompt: $prompt

Source content:
$content

Generate exactly $slide_count cards. Audience level: $level.

Return ONLY this JSON (no markdown fences, no commentary):
{
  "title": "Deck title (8 words or fewer)",
  "cards": [
    {
      "role": "title|content|closing",
      "title": "Card heading",
      "intro": "Optional 1-2 sentence framing line under the heading (empty string if none)",
      "wantsImage": true,
      "imagePrompt": "If wantsImage: one sentence describing a content-specific illustration of THIS card's subject. Else empty string.",
      "body": { "kind": "callouts", "items": [] }
    }
  ]
}

body.kind options and their items shape — pick the one that fits each card:
- "callouts":    items = [{"label":"2-4 words","text":"one concrete sentence"}]    (3-4 items)
- "iconlist":    items = [{"icon":"<name>","label":"2-4 words","text":"one sentence"}]   (3-6 items)
- "subsections": items = [{"heading":"3-5 words","text":"2-3 sentence paragraph"}]   (2-3 items)
- "bullets":     items = ["concrete point of 10-18 words"]   (3-5 items)
- "stat":        items = [{"value":"73%","label":"what it measures"}]   (3-4 items; value must contain a number)
- "chart":       items = {"chartType":"bar|line|pie","data":[{"label":"...","value":12}]}   (4-7 points)
- "table":       items = {"headers":["Col A","Col B"],"rows":[["...","..."],["...","..."]]}   (2-4 columns, 3-6 rows)
- "roadmap":     items = [{"phase":"Q1 2026 or Phase 1","label":"the milestone, 6-12 words"}]   (3-5 items)
- "comparison":  items = {"left":{"label":"Side A","items":["...","..."]},"right":{"label":"Side B","items":["...","..."]}}   (3-5 items per side)
- "quote":       items = {"quote":"12-25 words","attribution":"name, role"}
- "paragraph":   items = "a 2-4 sentence paragraph"

Allowed icon names (use ONLY these): activity, award, bar-chart, brain, check,
clock, cloud, compass, cpu, database, eye, flag, gauge, globe, heart, layers,
leaf, lightbulb, lock, map, rocket, settings, shield, sparkles, star, target,
trending-up, users, zap, workflow, wrench, book

RULES:
- cards length = exactly $slide_count.
- cards[0].role = "title": punchy heading + an intro that reads as a subtitle;
  body.kind = "paragraph" with a 1-2 sentence items string.
- last card role = "closing": a forward-looking wrap-up; body.kind = "paragraph"
  or "bullets".
- every other card role = "content". NO agenda or table-of-contents card.
- Match each card's body.kind to what its content actually IS — never force a
  type. Use "chart" for comparable numbers or trends, "table" for structured
  multi-column data, "roadmap" for phased / sequential / timeline content,
  "comparison" for genuinely two-sided topics, "stat" for standalone metrics.
  Where a card is narrative, callouts / subsections / iconlist / bullets fit.
  A 6+ card deck will naturally include a chart or table — but only where the
  content calls for it, never as filler.
- VARY body.kind — never the same kind on two cards in a row. Across the deck
  use a real mix of callouts, iconlist, subsections, bullets, stat, chart, table,
  roadmap, comparison, quote.
- IMAGES: set wantsImage = true on roughly HALF the cards — those where a
  content illustration genuinely helps the reader (a concept, process,
  comparison, organism, anatomy, mechanism, or a hero image for the title card).
  imagePrompt must describe an illustration of THAT card's real subject — never
  generic decoration. Set wantsImage = false on stat, chart, table, roadmap, and
  comparison cards — they are already visual or structured.
- Write like a domain expert: specific terminology, realistic figures, concrete
  named examples. No vague filler. Keep every card substantive and detailed. If
  the source is only a short topic, expand it with accurate domain knowledge.
- Emphasis: wrap key terms in **double asterisks** and term/title names in
  *single asterisks*.
- Every card covers a DISTINCT angle. Never repeat a fact across cards.

Respond with valid JSON only.
""")


FLOW_CARD_PROMPT = Template("""
You are an expert presentation designer. Regenerate ONE card for an existing
Gamma-style deck — flexible-height cards, each a distinct composition.

Deck topic: $prompt
Deck title: $deck_title
This is card $card_number of $total_cards. Audience level: $level.

Return ONLY this JSON for the single card (no markdown fences, no commentary):
{
  "role": "title|content|closing",
  "title": "Card heading",
  "intro": "Optional 1-2 sentence framing line (empty string if none)",
  "wantsImage": false,
  "imagePrompt": "",
  "body": { "kind": "callouts", "items": [] }
}

body.kind options and their items shape — pick the one that fits the card:
- "callouts":    items = [{"label":"2-4 words","text":"one concrete sentence"}]   (3-4 items)
- "iconlist":    items = [{"icon":"<name>","label":"2-4 words","text":"one sentence"}]   (3-6 items)
- "subsections": items = [{"heading":"3-5 words","text":"2-3 sentence paragraph"}]   (2-3 items)
- "bullets":     items = ["concrete point of 10-18 words"]   (3-5 items)
- "stat":        items = [{"value":"73%","label":"what it measures"}]   (3-4 items)
- "chart":       items = {"chartType":"bar|line|pie","data":[{"label":"...","value":12}]}   (4-7 points)
- "table":       items = {"headers":["Col A","Col B"],"rows":[["...","..."]]}   (2-4 cols, 3-6 rows)
- "roadmap":     items = [{"phase":"Q1 2026","label":"the milestone, 6-12 words"}]   (3-5 items)
- "comparison":  items = {"left":{"label":"Side A","items":["..."]},"right":{"label":"Side B","items":["..."]}}
- "quote":       items = {"quote":"12-25 words","attribution":"name, role"}
- "paragraph":   items = "a 2-4 sentence paragraph"

Allowed icon names (use ONLY these): activity, award, bar-chart, brain, check,
clock, cloud, compass, cpu, database, eye, flag, gauge, globe, heart, layers,
leaf, lightbulb, lock, map, rocket, settings, shield, sparkles, star, target,
trending-up, users, zap, workflow, wrench, book

RULES:
- Return ONE card object only — never an array and never a full deck.
- Match body.kind to what the content actually is. Write like a domain expert:
  specific terminology, realistic figures, concrete examples, no filler.
- Wrap key terms in **double asterisks**.

Respond with valid JSON only.
""")


FLOW_CARD_EDIT_PROMPT = Template("""
You are an AI editor for a single Gamma-style presentation card. The card is a
tree of blocks: container blocks ("stack", "row", "columns") hold children;
leaf blocks ("heading", "text", "bullets", "stat", "quote", "callout", "icon",
"chart", "table", "divider", "spacer", "image") hold content.

Current card (JSON):
$card_json

User request: "$message"

Apply ONLY the requested change and return this JSON (no markdown fences):
{
  "card": <the full modified card JSON, same structure as the input>,
  "reply": "<a 1-2 sentence confirmation of what changed>"
}

Rules:
- Keep the card's "id", "order", and "background" unchanged unless the request
  is explicitly about them.
- Keep the block tree valid: every block keeps its "id" and "type"; containers
  keep "children"; leaves keep "content".
- For text changes, edit the "content" of the relevant leaf blocks.
- Do not restructure or delete blocks unless the user asks for it.
- Return valid JSON only.
""")


def render(template: Template, **kwargs) -> str:
    return template.substitute(**kwargs)


# Level-specific guidance injected into COMBINED_GENERATION_PROMPT.
# Maps to slide types the renderer supports: title, agenda, content, stats, quote, closing.
LEVEL_INSTRUCTIONS_SIMPLE = """\
Style: clean, focused, text-driven — substantive content, no data-visualisation
slide types. Simple does NOT mean thin: every slide must be genuinely rich.
- Use these slide types: title, agenda, content, closing, plus "stats" and
  "quote" where the source genuinely supports them.
- Do NOT use "chart", "roadmap", "comparison", "kanban", or "funnel" — those
  belong to the advanced level.
- "content" slides must be RICH — follow the CONTENT DEPTH rules above exactly:
  4 substantive "Label: explanation" bullets (12-22 words each, every one a
  concrete, specific, informative sentence) AND a 1-2 sentence framing line in
  the body field. Never 3-word fragments. Never a half-empty slide.
- Never invent data. No marketing fluff or hype — just clear, informative,
  expert-level content.
- image_prompt: keep "" at this level.
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
