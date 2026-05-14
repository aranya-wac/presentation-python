# Phase 4 — Templates API & File Extractors

**Goal:** Public templates endpoints return seeded data. ExtractorFactory turns any `.txt`/`.docx`/`.pdf` into clean text + word/char counts.
**Estimate:** 1 day
**Blocks:** Phase 6 (generation needs both).

---

## Milestone 4.1 — Template schemas

- [ ] [`app/schemas/template.py`](../app/schemas/template.py):
  - `TemplateListItem` — id, name, category, description, thumbnail_url, total_slides (from metadata), preview_presentation_id, tags
  - `TemplateDetail` — full template with embedded `theme: ThemeSchema`
  - `ThemeSchema` — colors + fonts (mirror of Theme)
  - `TemplatePreviewResponse` — presentation_id, title, slides (full block list), theme

## Milestone 4.2 — Template service

- [ ] [`app/services/template_service.py`](../app/services/template_service.py):
  - `async def list_templates() -> list[TemplateListItem]` — `Template.find(Template.is_active == True).to_list()` mapped to schema
  - `async def get_template(id: str) -> TemplateDetail` — fetch + join theme; raises `NotFoundError`
  - `async def get_template_preview(id: str) -> TemplatePreviewResponse`:
    - Fetch template
    - If `preview_presentation_id is None` → `NotFoundError("Preview not yet generated. Run seeder.")`
    - Fetch the preview Presentation; assert `is_preview=True`
    - Fetch Theme; build response
- [ ] Add cache wrappers (use `app/core/cache.py`):
  - List → key `templates:list`, TTL 300s
  - Detail → key `templates:detail:{id}`, TTL 300s
  - Preview → key `templates:preview:{id}`, TTL 600s
  - Cache miss → fetch → set; cache hit → return decoded

## Milestone 4.3 — Templates router

- [ ] [`app/api/v1/templates.py`](../app/api/v1/templates.py) — exactly per spec §7:
  - `GET /templates` → 200 list (no auth)
  - `GET /templates/{id}` → 200 detail (no auth)
  - `GET /templates/{id}/preview` → 200 preview (no auth) — returns 404 if no preview yet
- [ ] Mount in `app/main.py` with prefix `/api/v1/templates`

## Milestone 4.4 — Manual e2e

- [ ] `curl /api/v1/templates` → 8 items
- [ ] `curl /api/v1/templates/<id>` → full detail with embedded theme
- [ ] `curl /api/v1/templates/<id>/preview` → 404 (no preview yet — expected pre-Phase 7)
- [ ] Second call to list — confirm cache hit (log it from `cache_get_json`)

---

## Milestone 4.5 — BaseExtractor + ExtractedContent

- [ ] [`app/extractors/base_extractor.py`](../app/extractors/base_extractor.py):
  - `class ExtractedContent(BaseModel)`:
    - `raw_text: str`
    - `metadata: ExtractedMetadata` with `total_words: int`, `total_chars: int`
  - `class BaseExtractor(ABC)`:
    - `@abstractmethod def extract(file_path: str) -> ExtractedContent`
    - `@staticmethod _build_metadata(text)` helper

## Milestone 4.6 — TextExtractor

- [ ] [`app/extractors/text_extractor.py`](../app/extractors/text_extractor.py):
  - Open with `encoding="utf-8", errors="replace"`
  - Strip BOM
  - Return `ExtractedContent`
- [ ] Edge cases: empty file → raises `ExtractionError("Empty file")`

## Milestone 4.7 — DocxExtractor

- [ ] [`app/extractors/docx_extractor.py`](../app/extractors/docx_extractor.py):
  - Use `python-docx`
  - Concatenate all paragraphs + table cells separated by `\n`
  - Skip empty paragraphs
- [ ] Test with a sample `.docx`

## Milestone 4.8 — PdfExtractor (with OCR fallback)

- [ ] [`app/extractors/pdf_extractor.py`](../app/extractors/pdf_extractor.py):
  - Try `pypdf.PdfReader` → join page texts
  - If `len(text.strip()) < 100` → OCR fallback:
    - Render each page to image (use `pdf2image` or `pypdfium2` — add to requirements.txt now)
    - Run `pytesseract.image_to_string(image)` on each
    - Concat
  - Document the tesseract binary requirement in README ("apt install tesseract-ocr" / brew etc.)

## Milestone 4.9 — ExtractorFactory

- [ ] [`app/extractors/extractor_factory.py`](../app/extractors/extractor_factory.py):
  - `class ExtractorFactory`:
    - `@staticmethod def for_file(path: str | Path) -> BaseExtractor`
    - Switch on suffix `.txt|.docx|.pdf` → return matching instance
    - Else → `ExtractionError("Unsupported file type")`
- [ ] Quick test: feed all three sample files → all return non-empty text

---

## Dependencies

- Phase 3 (templates seeded; Theme & Template models exist)
- Phase 1 (cache, exceptions)

## Parallelizable

**Major win:** the two halves of this phase are completely independent:
- 4.1 → 4.4 (Templates API) — depends on Phase 3 only
- 4.5 → 4.9 (Extractors) — depends on Phase 1 only

Build them in alternating sessions. Both halves can also run in parallel with Phase 5 (AI Agents).

## Sequential

- Inside the templates half: 4.1 → 4.2 → 4.3 → 4.4
- Inside the extractors half: 4.5 → (4.6, 4.7, 4.8 in parallel) → 4.9

## Definition of Done

- All three template endpoints respond per spec
- Cache hit visible in logs on repeated request
- ExtractorFactory yields correct text for `.txt`, `.docx`, `.pdf` (including a scanned PDF for OCR path)
- Unsupported extension → 422 with clear message

## Outputs

- Public templates browsing API
- Pluggable extractor system with factory
- Cache layer in active use

## Handoff to Phase 6

Phase 6 assumes:
- `template_service.get_template(id)` returns full template + theme
- `ExtractorFactory.for_file(path).extract(path)` returns clean text without raising on edge cases
- Cache helpers are tested under load-ish conditions
