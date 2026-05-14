# Phase 3 — Data Models & Static Seeding

**Goal:** All Beanie document models defined and registered. Themes (5) and Templates (8) seeded into Mongo from JSON files. Preview generation deferred to Phase 7.
**Estimate:** 1.5 days
**Blocks:** Phase 4 (templates API), Phase 6 (generation), Phase 7 (preview seeding).

---

## Milestone 3.1 — Remaining Beanie models

- [ ] [`app/models/theme.py`](../app/models/theme.py) — per spec §5:
  - `name: str` (Indexed, unique)
  - Nested Pydantic models: `Colors`, `FontSpec`, `Fonts`
  - `colors: Colors`, `fonts: Fonts`, `created_at: datetime`
- [ ] [`app/models/template.py`](../app/models/template.py):
  - `name`, `description`, `category`, `tags: list[str]`, `thumbnail_url`, `theme_id: PydanticObjectId`, `is_active: bool = True`
  - `metadata: TemplateMetadata` (nested Pydantic: `total_slides`, `estimated_duration`, `default_audience`)
  - `slides: list[dict]` — keep loose; spec slides are template structures with `[Placeholder]` strings, not strict schemas at this layer
  - `preview_presentation_id: Optional[PydanticObjectId] = None`
  - `created_at`
- [ ] [`app/models/presentation.py`](../app/models/presentation.py):
  - Nested Pydantic models: `Position`, `Styling`, `Block`, `Slide`
  - `Block.type` is loose-string (text/image/shape/etc.); validate at write boundary, not here
  - Document fields: `user_id: Optional[PydanticObjectId]`, `template_id`, `theme_id`, `title`, `description`, `logo_url: str`, `slides: list[Slide]`, `is_preview: bool = False`, timestamps
  - Indexes: compound `(user_id, is_preview, created_at)` for list query performance
- [ ] [`app/models/generation_job.py`](../app/models/generation_job.py):
  - Fields per spec §5
  - `status: Literal["pending","processing","completed","failed"]`
  - `progress: int = 0`
  - `gemini_response: Optional[dict]`, `error_message: Optional[str]`
  - `result_presentation_id: Optional[PydanticObjectId]`
  - Indexes: `user_id`, `status`
- [ ] [`app/models/export_job.py`](../app/models/export_job.py):
  - Fields per spec §5
  - `format: Literal["pdf","pptx","html"]`
  - `file_path`, `file_size: Optional[int]`
- [ ] Update `init_db(document_models=[User, RefreshToken, Theme, Template, Presentation, GenerationJob, ExportJob])`
- [ ] Boot app — verify zero index conflict warnings

## Milestone 3.2 — Theme seed JSONs (5 files)

- [ ] Create `seeds/themes/minimal_professional.json`
- [ ] Create `seeds/themes/creative_bold.json`
- [ ] Create `seeds/themes/tech_modern.json`
- [ ] Create `seeds/themes/elegant_classic.json`
- [ ] Create `seeds/themes/startup_energy.json`

Each file MUST contain the full structure:
```json
{
  "name": "Minimal Professional",
  "colors": { "primary": "#1F2937", "secondary": "#6B7280", "accent": "#3B82F6", "background": "#FFFFFF", "text": "#111827" },
  "fonts": {
    "heading": { "family": "Inter", "size": 48, "weight": 700 },
    "body":    { "family": "Inter", "size": 18, "weight": 400 },
    "caption": { "family": "Inter", "size": 14, "weight": 400 }
  }
}
```

Use distinct color palettes per theme — pick from any reputable design system. Validate every JSON parses.

## Milestone 3.3 — Template seed JSONs (8 files)

- [ ] `seeds/templates/business_pitch.json`
- [ ] `seeds/templates/startup_deck.json`
- [ ] `seeds/templates/product_launch.json`
- [ ] `seeds/templates/quarterly_review.json`
- [ ] `seeds/templates/project_proposal.json`
- [ ] `seeds/templates/marketing_plan.json`
- [ ] `seeds/templates/sales_presentation.json`
- [ ] `seeds/templates/training_module.json`

Each file structure:
```json
{
  "name": "Business Pitch",
  "description": "...",
  "category": "business",
  "tags": ["pitch","investors","funding"],
  "thumbnail_url": "/static/thumbnails/business_pitch.png",
  "theme_name": "Minimal Professional",
  "metadata": { "total_slides": 10, "estimated_duration": 15, "default_audience": "Investors" },
  "slides": [ /* see below */ ]
}
```

Each slide structure (spec §5 + logo block on slide 1):
```json
{
  "order": 1,
  "type": "title",
  "blocks": [
    { "id": "logo_block", "type": "image", "content": "[LOGO_PLACEHOLDER]", "position": { "x": 40, "y": 40, "w": 120, "h": 120 } },
    { "id": "title_block", "type": "text", "content": "[TITLE]", "position": { "x": 200, "y": 400, "w": 1520, "h": 200 } },
    { "id": "subtitle_block", "type": "text", "content": "[SUBTITLE]", "position": { "x": 200, "y": 620, "w": 1520, "h": 100 } }
  ]
}
```

Rules:
- Slide 1 of every template MUST contain the `[LOGO_PLACEHOLDER]` block exactly as in spec §14
- Use `[Placeholder]`-style content tokens (`[TITLE]`, `[BULLET_1]`, `[STAT]`, etc.) — no real content
- `total_slides` in metadata MUST equal `len(slides)`
- All positions in 1920×1080 pixel space (spec §16)
- Reference `theme_name` (string) — the seeder resolves it to `theme_id`

## Milestone 3.4 — Seed runner (themes + templates only)

- [ ] Implement [`seeds/seed_runner.py`](../seeds/seed_runner.py):
  - `async def seed_themes()`: glob `seeds/themes/*.json`, load each, `await Theme.find_one(Theme.name == name)` → upsert
  - `async def seed_templates()`: glob `seeds/templates/*.json`, resolve `theme_id` from theme name, upsert by template name (preserve existing `preview_presentation_id` if set)
  - `async def seed_previews()`: stub for now — Phase 7 implements
  - `async def main()`:
    - Connect to Mongo with `init_beanie(...)`
    - Run themes → templates → previews
    - Print counts
  - `if __name__ == "__main__": asyncio.run(main())`
- [ ] Run: `python seeds/seed_runner.py`
- [ ] Verify in Mongo: 5 themes, 8 templates, every template has a valid `theme_id`
- [ ] Re-run: same counts, no duplicates (idempotent)

## Milestone 3.5 — Validators

- [ ] In [`app/utils/validators.py`](../app/utils/validators.py):
  - `validate_template_slides(slides: list) -> None` — checks slide 1 has `[LOGO_PLACEHOLDER]` block, every block has `id/type/content/position`, position has `x/y/w/h` ≥ 0, slide 1 has `order=1`
  - Used by seeder before inserting; also reusable by Phase 7 PreviewGen output check

---

## Dependencies

- Phase 1 (DB init), Phase 2 (TimestampedDocument base, init_db pattern)

## Parallelizable

- 3.2 and 3.3 — JSON authoring, do them in parallel sessions
- 3.1 model files — write all five together, they don't import each other

## Sequential

- 3.4 needs 3.1 + 3.2 + 3.3
- 3.5 needs 3.1

## Definition of Done

- `python seeds/seed_runner.py` exits 0 with counts: themes=5, templates=8
- Re-running prints "skipped" / no inserts
- Direct Mongo query: every template has a valid `theme_id` referencing an existing theme
- Slide 1 of every template contains the logo block

## Outputs

- 7 Beanie document models registered
- 5 themes + 8 templates in Mongo
- Idempotent seeder
- Slide structure validator

## Handoff to Phase 4

Phase 4 assumes:
- `Template` and `Theme` queryable via Beanie
- Each template's `slides` is a list of dicts with `[Placeholder]` strings
- `preview_presentation_id` may be `None` (Phase 7 fills it)
