# Phase 10 — Testing, Hardening & Production Readiness

**Goal:** Confidence to deploy. Tests cover critical paths. Errors are categorized. Service is dockerized and observable.
**Estimate:** 1.5 days
**Blocks:** Production launch.

---

## Milestone 10.1 — Test infrastructure

- [ ] [`tests/conftest.py`](../tests/conftest.py):
  - `event_loop` fixture (session scope) for pytest-asyncio
  - `mongo_client` fixture using a **separate test database** (`presentation_deck_test`)
  - `init_db_for_tests` fixture — initializes Beanie with all models against test DB
  - `clean_db` fixture — `drop_collection` for each model after every test
  - `client` fixture — `httpx.AsyncClient(app=app, base_url="http://test")`
  - `auth_headers(user)` helper that issues a real JWT
- [ ] Configure `pytest.ini` (or `pyproject.toml [tool.pytest.ini_options]`):
  - `asyncio_mode = auto`
  - `testpaths = ["tests"]`
- [ ] Test runs against a **local Mongo** (Docker container) — never against Atlas prod

## Milestone 10.2 — Unit tests

- [ ] [`tests/unit/test_extractors.py`](../tests/unit/test_extractors.py):
  - TXT, DOCX, PDF (text-based), PDF (scan → OCR fallback)
  - Unsupported extension → `ExtractionError`
- [ ] [`tests/unit/test_template_mapper.py`](../tests/unit/test_template_mapper.py):
  - Logo replacement on slide 1 only
  - All `[Placeholder]` tokens resolved
  - Theme styling correctly copied to each block
  - Bullets fill multiple `[BULLET_*]` tokens by index
- [ ] [`tests/unit/test_security.py`](../tests/unit/test_security.py):
  - hash + verify round-trip
  - access vs refresh token type checked
  - Expired token rejected
- [ ] [`tests/unit/test_validators.py`](../tests/unit/test_validators.py):
  - Missing logo block on slide 1 → fails
  - Negative position → fails

## Milestone 10.3 — Integration tests

- [ ] [`tests/integration/test_auth_flow.py`](../tests/integration/test_auth_flow.py):
  - register → login → me → refresh → revoked refresh → 401
- [ ] [`tests/integration/test_templates.py`](../tests/integration/test_templates.py):
  - Seed minimal data in fixture, list/detail/preview endpoints
- [ ] [`tests/integration/test_generation.py`](../tests/integration/test_generation.py):
  - Mock Gemini to return fixed JSON (use `monkeypatch` on `GeminiClient.generate_json`)
  - POST /generate with TXT → poll until completed → verify Presentation persisted
  - Cross-user status access → 403
- [ ] [`tests/integration/test_presentations.py`](../tests/integration/test_presentations.py):
  - Owner CRUD; cross-user 403; preview mutation 403
- [ ] [`tests/integration/test_export.py`](../tests/integration/test_export.py):
  - Each format: result file exists, size > 0, magic bytes valid (`%PDF`, `PK` for pptx, `<!DOCTYPE` for html)

## Milestone 10.4 — Celery test mode

- [ ] In test settings, set `celery_app.conf.task_always_eager = True` so tasks run inline in the test process
- [ ] No need for a real worker during tests

## Milestone 10.5 — Error handling polish

- [ ] Audit every `service` method — replace bare `Exception`/HTTPException with the typed `AppError` subclasses
- [ ] Audit every router — no try/except that swallows; rely on the global handler from Phase 1
- [ ] Add request-id middleware: generate UUID per request, log it, return as `X-Request-ID` header — makes log correlation possible
- [ ] Add basic rate limiting to `POST /generate` and `POST /export/*/{format}` (e.g., `slowapi` — 10/min/user). Document the limits.

## Milestone 10.6 — Logging hygiene

- [ ] Confirm zero `print()` calls in `app/`
- [ ] Confirm sensitive fields never logged: passwords, tokens, raw Gemini API key, full file contents
- [ ] At INFO: request start/finish, job state transitions
- [ ] At ERROR: stack traces with request_id

## Milestone 10.7 — Dockerization

- [ ] [`Dockerfile`](../Dockerfile) — multi-stage:
  - Stage 1 (builder): install deps into `/wheels`
  - Stage 2 (runtime): python:3.11-slim, install tesseract + pango + libpangoft2 + curl, copy wheels, install
  - Non-root user
  - `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`
- [ ] [`Dockerfile.worker`](../Dockerfile.worker) — same base, `CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=info"]`
- [ ] [`docker-compose.yml`](../docker-compose.yml) — services: `api`, `worker`, `mongo` (for local), `redis` (for local)
  - Use `.env` file (point to Atlas/Cloud in prod overrides)
- [ ] `docker compose up` → app boots, worker boots, /health returns 200

## Milestone 10.8 — Observability minimum

- [ ] `GET /health` returns DB ping + Redis ping status, not just static OK
- [ ] Structured logs to stdout (Docker captures these)
- [ ] (Optional now, recommended later) Prometheus exporter — out of scope for MVP

## Milestone 10.9 — Cleanup & GC

- [ ] Add a Celery beat (or simple cron) plan documented in README:
  - Daily: delete `storage/uploads/{job_id}/` for jobs older than 24h regardless of status (failed jobs already clean up; successful ones are no longer needed)
  - Weekly: delete `storage/exports/*` older than 30 days
  - Document but **do not implement** the scheduler — note in roadmap

## Milestone 10.10 — Documentation

- [ ] [`README.md`](../README.md) — final pass:
  - Quickstart (clone → venv → .env → run)
  - System dependencies (tesseract, pango)
  - How to run the seeder
  - How to run tests
  - How to deploy via Docker
  - Architecture diagram (ASCII or link to spec §4)
- [ ] Mark the spec file as the source of truth: link from README

## Milestone 10.11 — Pre-flight checklist

- [ ] All tests green
- [ ] Manual e2e from a fresh clone passes
- [ ] No TODO / FIXME left in `app/`
- [ ] `.env.example` has every var the app uses
- [ ] Secrets confirmed not in git history

---

## Dependencies

- Phases 0–9 complete

## Parallelizable

- Tests (10.1–10.4) can be built in any order
- Hardening (10.5–10.6) is an audit pass — straightforward
- Docker (10.7) and Docs (10.10) are independent

## Sequential

- 10.11 last

## Definition of Done

- Test suite passes locally and in `docker compose run api pytest`
- `docker compose up` starts API + worker successfully
- README walks a new dev to a working system in under 30 minutes
- Pre-flight checklist 100% checked

## Outputs

- Test suite: unit + integration
- Dockerfile + compose stack
- Health checks with real dependency probes
- Hardened error/logging/rate-limit posture
- Production-ready README

## Handoff (post-MVP)

Things knowingly deferred — track in roadmap:
- Celery beat for storage GC
- Prometheus / Grafana metrics
- WebSockets / SSE for real-time job updates (currently polled)
- S3-backed storage instead of local disk (storage helpers are abstracted enough to swap)
- Multi-tenancy / org accounts
- Per-template paid tier / billing
