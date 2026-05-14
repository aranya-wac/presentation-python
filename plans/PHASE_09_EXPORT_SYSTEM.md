# Phase 9 — Export System

**Goal:** A user with a presentation can request export to PDF, PPTX, or HTML and download the resulting file. Build order: HTML (simplest) → PPTX → PDF.
**Estimate:** 2 days
**Blocks:** Final user experience.

---

## Milestone 9.1 — Export schemas + service skeleton

- [ ] [`app/schemas/export.py`](../app/schemas/export.py):
  - `ExportStartResponse(job_id, status)` — status="pending"
  - `ExportStatusResponse(job_id, status, progress, file_url: Optional[str], file_size: Optional[int], error_message: Optional[str])`
- [ ] [`app/services/export_service.py`](../app/services/export_service.py):
  - `async def create_export_job(presentation_id, user_id, format) -> ExportJob`:
    - Validate user owns presentation (`presentation_service.get_owned(...)`)
    - Insert ExportJob with `status="pending"`, `progress=0`, format
    - Enqueue `export_presentation_task.delay(job_id, presentation_id, format, user_id)`
  - `async def get_export_job(job_id, user_id) -> ExportJob` — ownership check
  - `def file_url_for(job: ExportJob) -> str` → `/api/v1/export/download/{job.id}`

## Milestone 9.2 — Export router

- [ ] [`app/api/v1/export.py`](../app/api/v1/export.py):
  - `POST /export/{presentation_id}/pdf` → 202
  - `POST /export/{presentation_id}/pptx` → 202
  - `POST /export/{presentation_id}/html` → 202
  - `GET /export/jobs/{job_id}` → 200 status
  - `GET /export/download/{job_id}` → `FileResponse(path, filename=..., headers={"Content-Disposition": "attachment; filename=..."})` — 404 if job not done
  - All require auth + ownership
- [ ] Mount with `prefix="/api/v1/export"`

## Milestone 9.3 — HtmlExportAgent (Reveal.js)

- [ ] [`app/agents/export/html_export_agent.py`](../app/agents/export/html_export_agent.py):
  - Use Jinja2 template loaded from `app/agents/export/templates/reveal.html.j2`
  - Each slide → `<section>` with absolute-positioned divs for blocks
  - Inline CSS using theme styling already baked into block.styling
  - Logo `<img>` on slide 1 from `/logo/main_logo.png` (embed as base64 so the file is self-contained)
  - Reveal.js loaded from CDN (note in spec: standalone HTML — base64 the JS too if standalone is strict; otherwise CDN is fine for MVP — document the choice)
  - Output canvas 1920×1080 — set Reveal `width: 1920, height: 1080`
  - Save to `storage/exports/{presentation_id}.html`
  - Return file path
- [ ] Smoke: open the HTML in a browser → all slides render correctly with logo on slide 1

## Milestone 9.4 — PptxExportAgent

- [ ] [`app/agents/export/pptx_export_agent.py`](../app/agents/export/pptx_export_agent.py):
  - `python-pptx`
  - Slide size: `prs.slide_width = Emu(1920 * 9525)`, `prs.slide_height = Emu(1080 * 9525)` (1 px = 9525 EMU at 96 DPI; verify with `Inches`/`Pt` per python-pptx best practice)
  - For each slide:
    - Use blank layout
    - For each block:
      - text → `add_textbox(left, top, width, height)` with paragraph runs styled from `block.styling` (font_family, font_size, color, bold from font_weight ≥ 600)
      - image → `add_picture(path, left, top, width, height)`
        - For `/logo/main_logo.png` → resolve to absolute `logo/main_logo.png` on disk
      - shape → `add_shape(MSO_SHAPE.RECTANGLE, ...)` filled with background_color (only if needed)
  - Position conversion: pixel → EMU helper `px_to_emu(px) -> Emu(int(px * 9525))`
  - Save to `storage/exports/{presentation_id}.pptx`
- [ ] Smoke: open in PowerPoint / LibreOffice — verify positions, fonts, logo

## Milestone 9.5 — PdfExportAgent (WeasyPrint)

- [ ] [`app/agents/export/pdf_export_agent.py`](../app/agents/export/pdf_export_agent.py):
  - Render Jinja2 template `app/agents/export/templates/pdf.html.j2`:
    - One `<div class="slide">` per slide with `@page { size: 1920px 1080px; margin: 0; }` and `.slide { page-break-after: always; }`
    - Absolute-positioned blocks (CSS pixel coordinates straight from block.position)
    - Theme colors/fonts inlined per block from block.styling
    - Logo on slide 1: read `logo/main_logo.png` from disk and base64-embed (WeasyPrint can also resolve `file://` URLs)
  - WeasyPrint: `HTML(string=html).write_pdf(path)`
  - Document system deps in README: `apt-get install libpango-1.0-0 libpangoft2-1.0-0` (Debian/Ubuntu); brew variant for macOS
  - Save to `storage/exports/{presentation_id}.pdf`
- [ ] Smoke: open PDF in browser — verify pixel-accurate layout

## Milestone 9.6 — export_presentation_task

- [ ] [`app/tasks/export_tasks.py`](../app/tasks/export_tasks.py):
  - `@celery_app.task(bind=True, name="export_presentation", time_limit=180)`
  - `def export_presentation_task(self, job_id, presentation_id, format, user_id) -> str`
  - `async def _export(...)`:
    - 0% — fetch ExportJob, set status="processing"
    - 20% — fetch Presentation + Theme; verify ownership (defense in depth — should already be done at create)
    - 50% — pick agent by format; call `await agent.export(presentation_id)` (or sync via `to_thread` for python-pptx/weasyprint which are sync)
    - 90% — set `file_path`, `file_size = path.stat().st_size`
    - 100% — status="completed", `completed_at=now`
  - On exception: status="failed", `error_message`, log, re-raise
- [ ] Wire into Celery's autodiscover

## Milestone 9.7 — End-to-end test

- [ ] Generate a presentation (Phase 6)
- [ ] `POST /export/{id}/html` → poll status → `GET /export/download/{job_id}` → download HTML → verify renders
- [ ] Repeat for `pptx` and `pdf`
- [ ] Cross-user attempt: user B tries `POST /export/{user_A_presentation}/pdf` → 403
- [ ] Force a failure (bad presentation_id) → status="failed" with message

---

## Dependencies

- Phase 6 (Celery infra, async-in-celery bridge)
- Phase 8 (`presentation_service.get_owned` for ownership)

## Parallelizable

- 9.3, 9.4, 9.5 are three independent agents — build in parallel sessions
- Within each agent: template authoring + Python wiring can be alternated
- Whole phase can be developed in parallel with Phase 8 (different files entirely)

## Sequential

- 9.1 → 9.2 → 9.6 → 9.7
- 9.7 requires all three agents merged

## Definition of Done

- All three formats download cleanly
- File sizes recorded correctly in ExportJob
- Logo present on slide 1 of every export format
- Layouts match the 1920×1080 canvas
- Cross-user export blocked

## Outputs

- Three working export agents
- Download endpoint serving FileResponse with proper Content-Disposition
- ExportJob lifecycle managed via Celery

## Handoff to Phase 10

Phase 10 must:
- Add tests covering all three export formats (at least: file is non-empty + valid magic bytes)
- Add cleanup policy for `storage/exports/` (orphaned files from deleted presentations)
- Document tesseract + pango system dependencies in deployment docs
