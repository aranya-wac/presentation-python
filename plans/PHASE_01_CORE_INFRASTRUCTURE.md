# Phase 1 — Core Infrastructure

**Goal:** Mongo connected via Beanie on app startup. Celery worker boots and can ping Redis. Reusable security/storage/exception primitives in place.
**Estimate:** 1 day
**Blocks:** Phases 2–10.

---

## Milestone 1.1 — MongoDB + Beanie

- [ ] Implement [`app/core/database.py`](../app/core/database.py):
  - `init_db(document_models: list)` — opens Motor client, calls `init_beanie(database=client.get_default_database(), document_models=...)`
  - `close_db()` — closes Motor client
- [ ] Wire into `app/main.py` lifespan (document_models list is empty for now — Phase 3 populates)
- [ ] Smoke: app starts, no Mongo errors logged

## Milestone 1.2 — Redis + Celery

- [ ] Implement [`app/tasks/celery_app.py`](../app/tasks/celery_app.py):
  - `celery_app = Celery("presentation_deck", broker=settings.REDIS_URL, backend=settings.REDIS_URL)`
  - `celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"], task_track_started=True, task_time_limit=300, broker_connection_retry_on_startup=True)`
  - Auto-discover tasks: `celery_app.autodiscover_tasks(["app.tasks"])`
- [ ] Add a throwaway `@celery_app.task ping() -> "pong"` for boot test (delete after Phase 6)
- [ ] `celery -A app.tasks.celery_app worker --loglevel=info` connects to Redis Cloud cleanly
- [ ] From a Python REPL: `ping.delay().get(timeout=5) == "pong"`

## Milestone 1.3 — Security primitives

- [ ] Implement [`app/core/security.py`](../app/core/security.py):
  - `hash_password(plain: str) -> str` (bcrypt via passlib)
  - `verify_password(plain: str, hashed: str) -> bool`
  - `create_access_token(subject: str, expires_minutes: int) -> str`
  - `create_refresh_token(subject: str, expires_days: int) -> str`
  - `decode_token(token: str) -> dict` (raises on invalid)
  - All JWT use HS256 + `settings.SECRET_KEY`
  - `iat`, `exp`, `sub`, and `type` ("access"|"refresh") in payload

## Milestone 1.4 — Exceptions

- [ ] Implement [`app/core/exceptions.py`](../app/core/exceptions.py):
  - `AppError(Exception)` base — fields: `status_code: int`, `detail: str`
  - Subclasses: `NotFoundError(404)`, `AuthError(401)`, `ForbiddenError(403)`, `ValidationError(422)`, `GeminiError(502)`, `ExtractionError(422)`, `JobError(500)`
- [ ] Register a global handler in `app/main.py` that converts `AppError` → JSON response with `{detail}`

## Milestone 1.5 — Storage helpers

- [ ] Implement [`app/core/storage.py`](../app/core/storage.py):
  - `UPLOADS_DIR = Path("storage/uploads")`, `EXPORTS_DIR = Path("storage/exports")`
  - `ensure_dirs()` — called from lifespan
  - `save_upload(job_id: str, filename: str, file: UploadFile) -> Path` — streams to `UPLOADS_DIR/{job_id}/{filename}`, enforces `MAX_UPLOAD_SIZE_MB`
  - `delete_upload(job_id: str)` — recursive rmtree of `UPLOADS_DIR/{job_id}`
  - `export_path(presentation_id: str, fmt: str) -> Path`
- [ ] Wire `ensure_dirs()` into lifespan

## Milestone 1.6 — Cache helper (thin)

- [ ] Implement [`app/core/cache.py`](../app/core/cache.py):
  - Single async Redis client (`redis.asyncio`) using `settings.REDIS_URL`
  - `get_cache() -> Redis` returning the singleton
  - Helpers `cache_get_json(key)`, `cache_set_json(key, value, ttl=300)`
- [ ] Currently unused by routes — exists so Phase 4 can cache template list

## Milestone 1.7 — Logger

- [ ] Implement [`app/utils/logger.py`](../app/utils/logger.py):
  - `get_logger(name: str) -> logging.Logger`
  - JSON formatter (timestamp, level, name, message, extra fields)
  - Configure root logger from lifespan once, level from env (default INFO)

---

## Dependencies

- Phase 0 complete (settings, app shell, package layout)

## Parallelizable

Within this phase:
- 1.1 + 1.2 can be done in parallel sessions (Mongo and Celery are independent)
- 1.3, 1.4, 1.5, 1.6, 1.7 are independent files — order by need

## Sequential

- 1.1 must finish before 1.2's lifespan integration test
- 1.5 needs 1.4 (uses exceptions on validation failure)

## Definition of Done

- `uvicorn ...` boots and the lifespan logs "DB connected, dirs ensured, logger initialized"
- `celery -A app.tasks.celery_app worker` runs, accepts the ping task, returns "pong"
- A throwaway script can `from app.core.security import hash_password; print(hash_password("x"))` without import errors
- `AppError` raised inside a test route renders as proper JSON

## Outputs

- Live Mongo connection on app boot
- Working Celery worker against Redis Cloud
- Reusable security, storage, exception, cache, logging modules

## Handoff to Phase 2

Phase 2 assumes:
- `init_db()` accepts a `document_models` list it will register `User` into
- `security.py` token + password helpers are stable (no signature changes)
- `AppError` taxonomy exists for `AuthError`/`ForbiddenError`
