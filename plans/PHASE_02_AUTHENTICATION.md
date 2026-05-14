# Phase 2 — Authentication

**Goal:** Register, login, refresh, and `me` endpoints work. Protected routes resolve `current_user` via JWT.
**Estimate:** 1 day
**Blocks:** Every authenticated route (Phases 6–9).

---

## Milestone 2.1 — User model

- [ ] Implement [`app/models/base.py`](../app/models/base.py):
  - `class TimestampedDocument(Document)` mixin with `created_at: datetime`, `updated_at: datetime`
  - `pre_save` hook sets `updated_at` on every save
- [ ] Implement [`app/models/user.py`](../app/models/user.py):
  - Beanie `Document` named `users` (Settings inner class)
  - Fields per spec §5: `email` (Indexed, unique), `hashed_password`, `full_name`, `role: Literal["user","admin"] = "user"`, `is_active: bool = True`
  - Inherits timestamps from base
- [ ] Implement [`app/models/refresh_token.py`](../app/models/refresh_token.py) (new — needed for revocation per spec §6):
  - Fields: `user_id`, `token` (Indexed unique), `expires_at`, `revoked: bool = False`
- [ ] Register both in `init_db(document_models=[User, RefreshToken])` via lifespan

## Milestone 2.2 — Auth schemas

- [ ] Implement [`app/schemas/auth.py`](../app/schemas/auth.py):
  - `RegisterRequest(email: EmailStr, password: str (min 8), full_name: str)`
  - `LoginRequest(email: EmailStr, password: str)`
  - `RefreshRequest(refresh_token: str)`
  - `TokenResponse(access_token, refresh_token, token_type="bearer", expires_in: int)`
  - `AccessTokenResponse(access_token: str, token_type="bearer", expires_in: int)`
  - `UserPublic(id, email, full_name, role, is_active)`

## Milestone 2.3 — Auth service

- [ ] Implement [`app/services/auth_service.py`](../app/services/auth_service.py) — pure async, no FastAPI imports:
  - `register(payload) -> User` — uniqueness check on email → `ValidationError` if exists
  - `authenticate(email, password) -> User` — `AuthError` on fail
  - `issue_tokens(user) -> TokenResponse` — creates access + refresh, persists refresh token row
  - `rotate_refresh(refresh_token: str) -> AccessTokenResponse` — verifies token signature + DB row + not revoked + not expired; returns new access token (refresh stays the same per spec)
  - `revoke_refresh(token: str)` — marks DB row revoked (used on logout — even though no logout endpoint in spec, leave the helper for later)

## Milestone 2.4 — Dependency: get_current_user

- [ ] Implement [`app/api/dependencies.py`](../app/api/dependencies.py):
  - `oauth2_scheme = HTTPBearer(auto_error=False)`
  - `async def get_current_user(creds = Depends(oauth2_scheme)) -> User`:
    - Missing credentials → `AuthError("Not authenticated")`
    - Decode token; reject if `type != "access"`
    - Load user by `sub` (user id); reject if not found or `is_active=False`
  - `async def get_current_admin(user: User = Depends(get_current_user)) -> User` — checks `role == "admin"`

## Milestone 2.5 — Auth router

- [ ] Implement [`app/api/v1/auth.py`](../app/api/v1/auth.py) — endpoints exactly per spec §7:
  - `POST /auth/register` → `201 UserPublic`
  - `POST /auth/login` → `200 TokenResponse`
  - `POST /auth/refresh` → `200 AccessTokenResponse`
  - `GET /auth/me` → `200 UserPublic` (requires `get_current_user`)
- [ ] Mount router in `app/main.py`: `app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])`

## Milestone 2.6 — Manual e2e

- [ ] `POST /api/v1/auth/register` with new email → 201 + user JSON
- [ ] Repeat same email → 422 with "email already exists"
- [ ] `POST /api/v1/auth/login` with wrong password → 401
- [ ] Login success → `access_token` + `refresh_token` returned
- [ ] `GET /auth/me` with `Authorization: Bearer <access>` → 200 with user
- [ ] `GET /auth/me` with no header → 401
- [ ] `POST /auth/refresh` with valid refresh → new access token; reuse the same refresh again → still works (per spec)
- [ ] Manually mark refresh row `revoked=True` in Mongo → `/refresh` returns 401

---

## Dependencies

- Phase 1 complete (security helpers, exceptions, DB init)

## Parallelizable

- 2.1 + 2.2 in parallel (model and schemas don't import each other)
- 2.3 must precede 2.5
- 2.4 must precede 2.5

## Sequential

- 2.5 (router) is the last thing — wires everything

## Definition of Done

- All four auth endpoints respond as specced
- Protected `/auth/me` test passes
- Refresh token revocation works at DB level
- No plaintext passwords ever stored or logged

## Outputs

- Working JWT auth + refresh
- Reusable `get_current_user` dependency
- `User` and `RefreshToken` Beanie models
- Auth service callable from any future code (Phase 6 generation needs `user_id`)

## Handoff to Phase 3

Phase 3 assumes:
- `User` is registered with Beanie
- `init_db()` document model list is the integration point — Phase 3 will append more
- `TimestampedDocument` base exists for reuse
