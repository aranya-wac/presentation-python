# Phase 0 — Foundation & Setup

**Goal:** A FastAPI app that boots with `uvicorn app.main:app --reload` and returns 200 on `/health`.
**Estimate:** 0.5 day
**Blocks:** Everything.

---

## Milestone 0.1 — Repo scaffold

- [ ] Create directory tree exactly as in spec §3 (every folder with a `.gitkeep` if empty)
- [ ] `git init`, add `.gitignore` (Python + `.env` + `storage/` + `__pycache__/` + `*.pyc` + `seeds/previews/`)
- [ ] Create empty `__init__.py` in every `app/**` subpackage
- [ ] Create `README.md` with the three run commands from spec §2

## Milestone 0.2 — Dependencies

- [ ] Pin Python 3.11 (`.python-version` or pyenv)
- [ ] Create `requirements.txt` with:
  - `fastapi`, `uvicorn[standard]`
  - `pydantic`, `pydantic-settings`
  - `beanie`, `motor`
  - `celery`, `redis`
  - `python-jose[cryptography]`, `passlib[bcrypt]`
  - `python-multipart` (file uploads)
  - `google-generativeai`
  - `python-docx`, `pypdf`, `pytesseract`, `Pillow`
  - `weasyprint`, `python-pptx`, `Jinja2`
  - `httpx` (for tests), `pytest`, `pytest-asyncio`
- [ ] Create `requirements-dev.txt`: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`
- [ ] `pip install -r requirements.txt -r requirements-dev.txt` in a fresh venv → must succeed clean
- [ ] Document venv activation in README

## Milestone 0.3 — Config skeleton

- [ ] Create `.env.example` with every variable listed in spec §15 — values blank/dummy
- [ ] Copy to `.env` locally with real Mongo Atlas + Redis Cloud + Gemini keys
- [ ] Implement [`app/config.py`](../app/config.py) — `Settings(BaseSettings)` with all env vars typed:
  - `MONGODB_URL: str`
  - `REDIS_URL: str`
  - `SECRET_KEY: str`
  - `GEMINI_API_KEY: str`
  - `ACCESS_TOKEN_EXPIRE_MINUTES: int = 30`
  - `REFRESH_TOKEN_EXPIRE_DAYS: int = 7`
  - `MAX_UPLOAD_SIZE_MB: int = 10`
  - `ALLOWED_ORIGINS: list[str]` (parse comma-separated)
- [ ] Export a module-level `settings = Settings()` instance

## Milestone 0.4 — FastAPI app shell

- [ ] Implement [`app/main.py`](../app/main.py):
  - `create_app() -> FastAPI` factory
  - CORS middleware reading `settings.ALLOWED_ORIGINS`
  - `lifespan` context (empty for now — Phase 1 fills it)
  - Mount `GET /health → {"status": "ok"}`
  - Mount `GET /` → 200 with API name + version
- [ ] Add API version constant `API_V1 = "/api/v1"` somewhere reusable

## Milestone 0.5 — Smoke test

- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] `curl http://localhost:8000/health` returns `{"status":"ok"}`
- [ ] `curl http://localhost:8000/docs` shows Swagger UI

---

## Dependencies

None — this is the bootstrap.

## Parallelizable

All milestones inside this phase are tiny — do them sequentially in one sitting.

## Definition of Done

- App boots clean
- `/health` returns 200
- All env vars load without `ValidationError`
- Swagger UI renders
- Repo committed with conventional structure

## Outputs

- Bootable FastAPI app
- Pinned dependency manifest
- Loaded typed `Settings`
- Empty package skeleton matching spec §3

## Handoff to Phase 1

Phase 1 assumes:
- `settings.MONGODB_URL` and `settings.REDIS_URL` are valid and reachable
- `app/main.py` has a `lifespan` to plug DB init into
- `app/core/` directory exists with `__init__.py`
