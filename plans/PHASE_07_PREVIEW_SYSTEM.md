# Phase 7 — Preview System & Full Seeding

**Goal:** Each of the 8 templates has a high-quality AI-generated preview presentation backed by a JSON cache file. `GET /templates/{id}/preview` returns 200 instead of 404.
**Estimate:** 0.5 day (mostly waiting on Gemini)
**Blocks:** None — pure value-add for the template browse UX.

---

## Milestone 7.1 — PreviewGeneratorAgent

- [ ] [`app/agents/generation/preview_generator_agent.py`](../app/agents/generation/preview_generator_agent.py):
  - `class PreviewGeneratorAgent`:
    - `__init__(self, gemini: GeminiClient, mapper: TemplateMapperAgent)`
    - `async def generate(self, template: Template, theme: Theme) -> list[MappedSlide]`:
      - Render `PREVIEW_GENERATION_PROMPT` with `{template_name, category, total_slides}`
      - Call `gemini.generate_json("pro", prompt)` → list of slide dicts
      - Wrap each into `AISlide`
      - Pass through `mapper.map(ai_slides, template, theme)` for full theme styling
      - Return mapped slides

## Milestone 7.2 — Preview file cache

- [ ] [`seeds/previews/`](../seeds/previews/) — already exists, gitignored per Phase 0
- [ ] In `seed_runner.py`, add helpers:
  - `def template_slug(name: str) -> str` — kebab-case (e.g., "Business Pitch" → "business_pitch")
  - `def preview_cache_path(slug: str) -> Path` → `seeds/previews/{slug}.json`
  - `def load_cached_preview(slug) -> Optional[list[dict]]`
  - `def save_cached_preview(slug, slides: list[dict])` — pretty JSON, 2-space indent

## Milestone 7.3 — seed_previews implementation

- [ ] In [`seeds/seed_runner.py`](../seeds/seed_runner.py), implement the `seed_previews()` stub from Phase 3 per spec §13:
  - For each Template:
    1. If `template.preview_presentation_id` already set AND the Presentation exists in DB → **skip**
    2. Else if cache file exists → load slides from disk (no Gemini call)
    3. Else → run `PreviewGeneratorAgent.generate(template, theme)` → save to cache file
    4. Insert `Presentation(user_id=None, template_id=template.id, theme_id=template.theme_id, title=f"{template.name} — Preview", description=template.description, logo_url="/logo/main_logo.png", slides=mapped, is_preview=True)`
    5. Update `template.preview_presentation_id = presentation.id` and save
  - Print "[Preview] template={name} status={created|cached|skipped}"
- [ ] Add `--force-rebuild` CLI flag that ignores caches and reseeds (handy when prompt template changes)

## Milestone 7.4 — Run the seeder

- [ ] First run: `python seeds/seed_runner.py` → expect 8 Gemini calls, 8 cache files written, 8 Presentations created
- [ ] Second run: → expect 8 "skipped" lines, zero Gemini calls
- [ ] Delete one cache file + clear that template's `preview_presentation_id` in Mongo → re-run → exactly one Gemini call
- [ ] Verify each `seeds/previews/*.json` is valid JSON and well-formed

## Milestone 7.5 — Preview endpoint regression

- [ ] `curl /api/v1/templates/<id>/preview` → 200 with full `slides[]`
- [ ] Confirm cache hit path in `template_service.get_template_preview` (Phase 4 Milestone 4.2)
- [ ] Slide 1 of every preview shows the logo URL in its first block

## Milestone 7.6 — Commit cache files

- [ ] Decide: **commit `seeds/previews/*.json` to source control** so co-developers don't re-spend Gemini quota on first run
- [ ] Update `.gitignore` to **un-ignore** `seeds/previews/*.json` (only the cache files; keep folder otherwise stable)
- [ ] Add a README note: "Cache files are committed. Delete + re-seed only if prompts/themes changed."

---

## Dependencies

- Phase 5 (GeminiClient, TemplateMapperAgent, AISlide schema)
- Phase 6 (proves the Mapper output round-trips into a Presentation cleanly)
- Phase 3 (seed_runner.py exists with stub)

## Parallelizable

- 7.1 and 7.2 can run together
- After 7.3 lands, 7.4–7.6 are sequential and quick

## Sequential

- 7.3 needs 7.1 + 7.2 + the Phase 5 mapper

## Definition of Done

- All 8 templates have a non-null `preview_presentation_id`
- All 8 cache files exist and are valid JSON
- Re-running the seeder is a no-op
- Preview endpoint returns rich, themed slides for any template

## Outputs

- Working PreviewGeneratorAgent
- 8 cached preview JSONs (committed)
- 8 preview Presentations in Mongo (`is_preview=True`, `user_id=None`)
- Idempotent end-to-end seeder

## Handoff to Phase 8

Phase 8 must:
- Filter `is_preview=True` out of the user-facing presentations list
- Block PUT/DELETE on previews (`presentation.user_id is None` → 403)
