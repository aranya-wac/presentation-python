# Phase 6 — Generation Pipeline (Celery)

**Goal:** A user uploads a `.txt`/`.docx`/`.pdf`, gets a `job_id`, and within ~60s a fully populated Presentation exists in Mongo. Status endpoint reflects progress in real time.
**Estimate:** 1.5 days
**Blocks:** Phase 7 (preview reuses parts), Phases 8 & 9 (need user presentations to exist).

---

## Milestone 6.1 — Generation schemas

- [ ] [`app/schemas/generation.py`](../app/schemas/generation.py):
  - `GenerationStartResponse(job_id: str, status: str)` — status always `"pending"`
  - `GenerationStatusResponse(job_id, status, progress, presentation_id: Optional[str], error_message: Optional[str])`

## Milestone 6.2 — File handler utility

- [ ] [`app/utils/file_handlers.py`](../app/utils/file_handlers.py):
  - `ALLOWED_EXTENSIONS = {".txt", ".docx", ".pdf"}`
  - `def validate_extension(filename: str) -> str` — returns lowercase ext or raises `ValidationError("Unsupported file type")`
  - `async def stream_to_disk(upload_file, dest_path) -> int` — chunked write, returns bytes written, enforces `MAX_UPLOAD_SIZE_MB` (raise `ValidationError` if exceeded mid-stream)

## Milestone 6.3 — Generation service

- [ ] [`app/services/generation_service.py`](../app/services/generation_service.py):
  - `async def create_job(user_id: ObjectId, template_id: str, upload_file) -> GenerationJob`:
    - Validate template exists + is_active
    - Validate file extension and size (via `file_handlers`)
    - Generate `job_id = uuid4().hex`
    - Save file → `storage/uploads/{job_id}/{filename}` via `core.storage.save_upload`
    - Insert `GenerationJob(status="pending", progress=0, ...)`
    - Enqueue Celery task `generate_presentation_task.delay(job_id, template_id, file_path, user_id)`
    - Return job
  - `async def get_job(job_id: str, user_id: ObjectId) -> GenerationJob` — ownership check or `ForbiddenError`
  - `async def update_progress(job_id: str, progress: int, **fields)` — partial update helper used by the Celery task

## Milestone 6.4 — Generation router

- [ ] [`app/api/v1/generation.py`](../app/api/v1/generation.py):
  - `POST /generate` (multipart):
    - `template_id: str = Form(...)`, `file: UploadFile = File(...)`
    - Auth required
    - Calls `generation_service.create_job` → returns 202 + `GenerationStartResponse`
  - `GET /generate/status/{job_id}`:
    - Auth required, ownership-checked
    - Returns `GenerationStatusResponse`
- [ ] Mount in `app/main.py` with prefix `/api/v1`

## Milestone 6.5 — Async-in-Celery bridge

- [ ] In [`app/tasks/celery_app.py`](../app/tasks/celery_app.py) add helper:
  - `def run_async(coro)` — `loop = asyncio.new_event_loop(); try: return loop.run_until_complete(coro) finally: loop.close()`
- [ ] In a Celery worker process, Beanie must be initialized once per worker:
  - Use Celery `worker_process_init` signal → `run_async(init_db([...all models...]))`
  - Same models list as FastAPI lifespan — extract to a shared `app/core/database_models.py` constant

## Milestone 6.6 — generate_presentation_task

- [ ] [`app/tasks/generation_tasks.py`](../app/tasks/generation_tasks.py):
  - `@celery_app.task(bind=True, name="generate_presentation", time_limit=120)`
  - Signature: `def generate_presentation_task(self, job_id, template_id, file_path, user_id) -> str`
  - Wraps `run_async(_generate(...))` for async logic
  - `async def _generate(...)`:
    - 0% — fetch job, set `status="processing"`
    - 10% — extract: `ExtractorFactory.for_file(file_path).extract(file_path)`
    - 20% — load Template + Theme (via service or direct Beanie)
    - 30% — `analysis = await ContentAnalyzerAgent(gemini).analyze(extracted.raw_text)`
    - 50% — `ai_slides = await SlideGeneratorAgent(gemini).generate(extracted.raw_text, analysis, template.metadata.total_slides, template.name)`
    - 70% — `mapped = TemplateMapperAgent().map(ai_slides, template, theme)`
    - 90% — Persist `Presentation(user_id, template_id, theme_id, title=analysis.topic, description=template.description, logo_url="/logo/main_logo.png", slides=mapped, is_preview=False)` → set `result_presentation_id`
    - 100% — `status="completed"`, `completed_at=now`, `gemini_response=analysis.dict()`
    - Update `progress` field after each step
  - On any exception:
    - Set `status="failed"`, `error_message=str(e)[:1000]`
    - Call `core.storage.delete_upload(job_id)`
    - Log full traceback via `app.utils.logger`
    - Re-raise so Celery records the failure

## Milestone 6.7 — End-to-end test (manual)

- [ ] Login → get token
- [ ] `curl -F template_id=<id> -F file=@sample.txt -H "Authorization: Bearer ..." http://localhost:8000/api/v1/generate` → 202 with job_id
- [ ] Poll `GET /generate/status/{job_id}` every 2s — observe progress climb 10/20/30/50/70/90/100
- [ ] Verify in Mongo: `Presentation` with `is_preview=False`, `user_id=<me>`, slides[0].blocks contains `/logo/main_logo.png`, all `[Placeholder]` tokens replaced
- [ ] Repeat with `.docx` and `.pdf` (including a scan-only PDF for OCR path)
- [ ] Force a failure (e.g., set `GEMINI_API_KEY=bad` in worker env) — confirm `status="failed"`, file deleted from `storage/uploads/{job_id}/`

## Milestone 6.8 — Cleanup

- [ ] Delete the throwaway `ping` task from Phase 1
- [ ] Delete `scripts/smoke_agents.py` from Phase 5

---

## Dependencies

- Phase 4 (template_service.get_template, ExtractorFactory)
- Phase 5 (all generation agents + GeminiClient)
- Phase 2 (current_user dependency)
- Phase 1 (storage helpers, exceptions)

## Parallelizable

- 6.1 + 6.2 in parallel
- 6.3 + 6.4 sequentially (router uses service)
- 6.5 + 6.6 in parallel with 6.4 once 6.3 done

## Sequential

- 6.6 → 6.7 (test)
- 6.7 must pass before declaring phase done

## Definition of Done

- A real `.pdf` upload produces a Presentation row with all placeholders filled and the logo present
- Failure path deletes the upload directory and writes `error_message`
- Worker logs show the progress checkpoints fired
- Status endpoint returns 403 when called by a different user

## Outputs

- Functional generation endpoint
- Working Celery worker pipeline
- Presentations created end-to-end from raw documents

## Handoff to Phase 7

Phase 7 reuses:
- Same Mapper / persistence shape (just with `is_preview=True, user_id=None`)
- Same model registration pattern in Celery worker init
- `core.storage` patterns — though Preview is JSON-cached on disk under `seeds/previews/`
