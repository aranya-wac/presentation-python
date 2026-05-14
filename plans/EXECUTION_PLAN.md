# Presentation Deck Studio — Master Execution Plan

**Spec source:** [`2026-04-27-presentation-deck-studio-backend-design.txt`](../2026-04-27-presentation-deck-studio-backend-design.txt)
**Owner:** Single developer + Claude assistance
**Strategy:** Vertical-slice MVP first (auth → templates → generate → export). Defer scale work to Phase 10.

---

## Phase Map

| # | Phase | Goal | Blocking? | Est. Days |
|---|---|---|---|---|
| 0 | [Foundation & Setup](PHASE_00_FOUNDATION.md) | Repo, deps, FastAPI shell boots | Yes | 0.5 |
| 1 | [Core Infrastructure](PHASE_01_CORE_INFRASTRUCTURE.md) | Mongo + Redis + Celery wired | Yes | 1 |
| 2 | [Authentication](PHASE_02_AUTHENTICATION.md) | JWT auth working end-to-end | Yes | 1 |
| 3 | [Data Models & Static Seeding](PHASE_03_DATA_MODELS_SEEDING.md) | All Beanie models + 5 themes + 8 templates seeded | Yes | 1.5 |
| 4 | [Templates API & File Extractors](PHASE_04_TEMPLATES_EXTRACTORS.md) | Templates endpoints + TXT/DOCX/PDF extraction | Partially parallel with P5 | 1 |
| 5 | [AI Agents Layer](PHASE_05_AI_AGENTS.md) | Gemini client + 3 agents callable in isolation | Partially parallel with P4 | 1.5 |
| 6 | [Generation Pipeline](PHASE_06_GENERATION_PIPELINE.md) | Upload file → Celery → presentation in DB | Yes | 1.5 |
| 7 | [Preview System](PHASE_07_PREVIEW_SYSTEM.md) | PreviewGeneratorAgent + full seeder + preview endpoint | After P6 | 0.5 |
| 8 | [Presentations CRUD](PHASE_08_PRESENTATIONS_CRUD.md) | List/get/update/delete with ownership | Parallel with P9 | 0.5 |
| 9 | [Export System](PHASE_09_EXPORT_SYSTEM.md) | HTML → PPTX → PDF export pipeline | Parallel with P8 | 2 |
| 10 | [Testing & Production Readiness](PHASE_10_TESTING_HARDENING.md) | Test coverage, Docker, observability | Final | 1.5 |

**Total estimate:** ~12 dev-days for solo + Claude.

---

## Critical Path (sequential)

```
P0 → P1 → P2 → P3 → P6 → P7 → (P8 ∥ P9) → P10
              ↓
            (P4 ∥ P5)  — both feed into P6
```

- P4 and P5 can be built in parallel by alternating sessions (extractors are pure Python; agents are I/O bound on Gemini).
- P8 and P9 are both downstream of P6 + P7 and don't share files — interleave freely.

---

## MVP Cut Line

The thinnest deployable cut is **P0 → P6 with only TXT extractor and only HTML export shortcut** — but the spec does not allow shortcuts, so the *real* MVP definition here is:

> Phases 0–7 complete + Phase 8 list/get + Phase 9 HTML export only.

That is the first demo-able milestone. PPTX/PDF and CRUD edits land in the next push.

---

## Cross-cutting Conventions

Apply across every phase — do not relitigate per file.

- **Async everywhere** in app code (Beanie, Motor, FastAPI handlers). Celery tasks may bridge to async via `asyncio.run()`.
- **Pydantic v2** for all schemas; `pydantic-settings` for `Settings`.
- **No upward imports.** `core/` imports nothing from `app/`. `services/` may not import `api/`. Tasks call services, never duplicate logic.
- **One agent class = one async public method.** Stateless. Constructor injection only.
- **Job IDs** are `uuid4().hex`. File paths derive from job_id, never from filename.
- **All Gemini prompts** end with `"Return ONLY valid JSON, no markdown fences."`
- **All Mongo writes** that mutate user data check `presentation.user_id == current_user.id` first.
- **Logging:** structured (JSON) via `app/utils/logger.py`. Never `print`.

---

## What This Plan Does NOT Cover

Out of scope per spec — flagged here so they don't sneak into a phase:

- Frontend (separate project)
- Logo upload (hardcoded `/logo/main_logo.png`)
- User-customized themes (themes are read-only)
- Template recommendation AI (user always picks)
- Preview editing (read-only)
- Multi-tenancy / organizations
- Billing / quotas
- Webhooks / SSE for job completion (poll `/status/{job_id}`)

If a stakeholder asks for any of these, it's a new phase — not a task slot inside an existing one.

---

## How To Use This Plan

1. Open the phase MD file.
2. Work top-to-bottom through the milestones.
3. Tick checkboxes as you complete tasks.
4. **Do not skip "Definition of Done" checks.** They are the handoff contract for the next phase.
5. If a task takes >2× its estimate, stop and reassess — don't grind.

Each phase MD ends with **Outputs** (what should exist when phase is done) and **Handoff to next phase** (what the next phase assumes is true).
