# Phase 8 — Presentations CRUD

**Goal:** Authenticated users list, fetch, edit, and delete only their own non-preview presentations.
**Estimate:** 0.5 day
**Blocks:** Frontend integration only.

---

## Milestone 8.1 — Schemas

- [ ] [`app/schemas/presentation.py`](../app/schemas/presentation.py):
  - `PresentationListItem` — id, title, template_name (denormalized lookup), total_slides, created_at, updated_at
  - `PresentationDetail` — full document including all slides + blocks
  - `PresentationUpdate` — partial: `title?`, `description?`, `slides?` (full replacement; no patching individual blocks in MVP)
  - All Pydantic v2 with `model_config = ConfigDict(from_attributes=True)`

## Milestone 8.2 — Presentation service

- [ ] [`app/services/presentation_service.py`](../app/services/presentation_service.py):
  - `async def list_for_user(user_id) -> list[PresentationListItem]`:
    - `Presentation.find(Presentation.user_id == user_id, Presentation.is_preview == False).sort(-Presentation.created_at).to_list()`
    - Resolve `template_name` via single batched query (avoid N+1)
  - `async def get_owned(presentation_id, user_id) -> Presentation`:
    - Fetch by id; raise `NotFoundError` if missing
    - If `presentation.is_preview is True` OR `presentation.user_id != user_id` → `ForbiddenError`
  - `async def update(presentation_id, user_id, payload: PresentationUpdate) -> Presentation`:
    - `get_owned(...)` for ownership
    - Validate `payload.slides` if present using `validators.validate_template_slides` (or a presentation-specific variant — slides require styling fields)
    - Apply partial update; bump `updated_at`
  - `async def delete(presentation_id, user_id) -> None`:
    - `get_owned(...)` for ownership
    - Delete document; do NOT cascade to export jobs (those reference file paths — leave alone, GC later)

## Milestone 8.3 — Router

- [ ] [`app/api/v1/presentations.py`](../app/api/v1/presentations.py):
  - `GET /presentations` → 200 list
  - `GET /presentations/{id}` → 200 detail
  - `PUT /presentations/{id}` → 200 updated detail
  - `DELETE /presentations/{id}` → 200 `{"message": "Deleted"}`
  - All require `get_current_user`
- [ ] Mount in `app/main.py`: `prefix="/api/v1/presentations"`

## Milestone 8.4 — Manual e2e

- [ ] As user A: generate presentation → confirm it appears in `GET /presentations`
- [ ] `GET /presentations/{id}` returns full slides + blocks
- [ ] As user B: `GET /presentations/{A_id}` → 403
- [ ] As user A: `PUT /presentations/{id}` with new title → 200, `updated_at` bumps
- [ ] As user A: `PUT /presentations/{id}` with new slides[] → 200, slides replaced
- [ ] As user A: `GET /presentations/{preview_id}` (a preview) → 403
- [ ] As user A: `DELETE /presentations/{id}` → 200, subsequent GET → 404

---

## Dependencies

- Phase 6 (user presentations exist to test against)
- Phase 7 (preview presentations exist to confirm 403 path)
- Phase 2 (auth)

## Parallelizable

- 8.1 + 8.2 in parallel
- This entire phase is parallel-safe with Phase 9 (different routers + services, different concerns)

## Sequential

- 8.3 needs 8.1 + 8.2
- 8.4 needs all above

## Definition of Done

- All four endpoints behave per spec §7
- Cross-user access blocked
- Preview presentations are read-only (403 on PUT/DELETE)
- Update accepts both metadata-only and full-slides payloads

## Outputs

- Complete CRUD for user presentations
- Ownership enforcement codified in `get_owned`

## Handoff to Phase 10

Test suite must cover:
- 403 cross-user
- 403 on preview mutation
- 200 on owner-only ops
