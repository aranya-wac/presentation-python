# Phase 5 — AI Agents Layer

**Goal:** GeminiClient is a working singleton. ContentAnalyzerAgent, SlideGeneratorAgent, and TemplateMapperAgent each callable in isolation from a REPL. PreviewGeneratorAgent is deferred to Phase 7.
**Estimate:** 1.5 days
**Blocks:** Phase 6.

---

## Milestone 5.1 — GeminiClient

- [ ] [`app/ai/gemini_client.py`](../app/ai/gemini_client.py):
  - `class GeminiClient` (singleton via module-level `_instance` + `get_gemini()`)
  - Constructor: `genai.configure(api_key=settings.GEMINI_API_KEY)`; create model handles for `"gemini-2.0-flash-lite"` and `"gemini-2.5-pro"`
  - `async generate_json(model: Literal["flash-lite","pro"], prompt: str) -> dict`:
    - Run model call in executor (`asyncio.to_thread`) — google-generativeai is sync
    - Strip markdown fences if present (`json.loads` after cleanup)
    - 3 retries with exponential backoff on `ResourceExhausted` / 429 / network errors
    - Raise `GeminiError` after final attempt with last error attached
  - Initialize singleton in app lifespan
- [ ] Quick REPL test: `await client.generate_json("flash-lite", 'Return JSON {"hi": "world"}. Return ONLY valid JSON, no markdown fences.')`

## Milestone 5.2 — Prompt templates

- [ ] [`app/ai/prompt_templates.py`](../app/ai/prompt_templates.py) — module-level constants only:
  - `CONTENT_ANALYSIS_PROMPT` — input vars: `{text}`. Asks for JSON `{topic, key_points: [], audience, tone}`
  - `SLIDE_GENERATION_PROMPT` — input vars: `{template_name, audience, tone, slide_count, content}`. Asks for JSON array of exactly `{slide_count}` slides each with `{order, type, title, body_text, bullets[]?, notes?}`
  - `PREVIEW_GENERATION_PROMPT` — input vars: `{template_name, category, total_slides}`. Asks for `{total_slides}` realistic sample slides
  - All prompts end with the literal: `"\n\nReturn ONLY valid JSON, no markdown fences."`
  - Provide a `render(template: str, **kwargs) -> str` helper using `str.format` (not Jinja — keep deps minimal here)

## Milestone 5.3 — ContentAnalyzerAgent

- [ ] [`app/agents/generation/content_analyzer_agent.py`](../app/agents/generation/content_analyzer_agent.py):
  - `class ContentAnalysis(BaseModel)`: `topic: str`, `key_points: list[str]`, `audience: str`, `tone: str`
  - `class ContentAnalyzerAgent`:
    - `__init__(self, gemini: GeminiClient)`
    - `async def analyze(self, text: str) -> ContentAnalysis`:
      - Truncate text to ~12k chars for flash-lite token budget
      - Render `CONTENT_ANALYSIS_PROMPT` with `{text}`
      - Call `gemini.generate_json("flash-lite", prompt)`
      - Parse into `ContentAnalysis`; raise `GeminiError` on schema mismatch

## Milestone 5.4 — SlideGeneratorAgent

- [ ] [`app/agents/generation/slide_generator_agent.py`](../app/agents/generation/slide_generator_agent.py):
  - `class AISlide(BaseModel)`:
    - `order: int`, `type: str`, `title: Optional[str]`, `body_text: Optional[str]`, `bullets: list[str] = []`, `notes: Optional[str]`
  - `class SlideGeneratorAgent`:
    - `__init__(self, gemini: GeminiClient)`
    - `async def generate(self, content: str, analysis: ContentAnalysis, slide_count: int, template_name: str) -> list[AISlide]`:
      - Render `SLIDE_GENERATION_PROMPT`
      - Call `gemini.generate_json("pro", prompt)`
      - Validate count: if drift > 1, raise `GeminiError`; if drift == 1, pad with empty slide or truncate
      - Renumber `order` 1..N to be safe

## Milestone 5.5 — TemplateMapperAgent (pure Python — no Gemini)

- [ ] [`app/agents/generation/template_mapper_agent.py`](../app/agents/generation/template_mapper_agent.py):
  - `class MappedSlide(BaseModel)` mirrors the Presentation `Slide` schema (re-export from `app/models/presentation.py` if convenient)
  - `class TemplateMapperAgent`:
    - `def map(self, ai_slides: list[AISlide], template: Template, theme: Theme) -> list[MappedSlide]`:
      - Iterate template.slides (the JSON skeleton)
      - For each block: if `content` matches `[LOGO_PLACEHOLDER]` → replace with `"/logo/main_logo.png"` (only on slide 1)
      - For each `[Placeholder]` token (`[TITLE]`, `[SUBTITLE]`, `[BULLET_N]`, `[STAT]`, `[BODY]`, `[NOTES]`, etc.) → fill from corresponding `AISlide` field; bullets fill multiple `[BULLET_*]` tokens by index
      - Compose `styling` for each block from theme (heading font for `type=text` and block id contains "title" or "heading"; body font otherwise; caption font for footers)
      - Copy `colors.text` → `color`, `colors.background` → `background_color` (when block id is a background block)
      - Default `text_align: "left"` unless block id implies otherwise
      - Position is preserved exactly from template.slides
  - Token resolver helper: `_resolve_token(token: str, ai_slide: AISlide, idx_hint: int) -> str | None`
  - Unfilled tokens → leave as empty string (do not crash)

## Milestone 5.6 — REPL smoke

Write `scripts/smoke_agents.py` (delete after Phase 6):
- [ ] Initialize Gemini singleton + connect Beanie
- [ ] Read sample text (paste from any blog post, ~2k words)
- [ ] Run analyzer → print `ContentAnalysis`
- [ ] Run generator → print `len(ai_slides)`
- [ ] Fetch any seeded template + theme
- [ ] Run mapper → assert each slide 1 block list contains `/logo/main_logo.png`
- [ ] Confirm zero placeholders remain in returned slides

---

## Dependencies

- Phase 1 (config, exceptions)
- Phase 3 (Theme, Template models — used by Mapper)

## Parallelizable

- 5.1 + 5.2 — independent files, write together
- 5.3, 5.4, 5.5 — independent classes, write in parallel sessions
- This entire phase can run in parallel with Phase 4 (templates API + extractors)

## Sequential

- 5.6 (smoke) needs all above

## Definition of Done

- `scripts/smoke_agents.py` runs end-to-end and prints sane output
- Mapper produces a list whose JSON serializes cleanly into a `Presentation.slides` field
- Logo URL appears exactly once per presentation, on slide 1
- Gemini retry path verified (manually trigger by setting `GEMINI_API_KEY=invalid` once → expect `GeminiError`)

## Outputs

- Reusable Gemini client with retry
- Three working agents
- Centralized prompt templates
- Smoke script proving the analyzer→generator→mapper pipeline

## Handoff to Phase 6

Phase 6 assumes:
- `gemini_client.get_gemini()` returns a singleton initialized at startup
- All three agents accept a `GeminiClient` (or no client for Mapper) and have stable async signatures
- `MappedSlide` matches `Presentation.Slide` so output can be persisted directly
