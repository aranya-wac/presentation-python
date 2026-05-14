"""Temporary admin endpoint for one-time seeding from a deployed environment.

Remove this file (and the include_router line in app/main.py) after seeding.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_token(token: str) -> None:
    expected = os.environ.get("SEED_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="SEED_TOKEN not configured on server")
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.post("/seed")
async def run_seed(
    token: str = Query(..., description="Must match SEED_TOKEN env var"),
    force_rebuild: bool = Query(False),
    skip_previews: bool = Query(True, description="Skip Gemini-based PPTX preview generation"),
    db: AsyncSession = Depends(get_db),
):
    _check_token(token)

    from seeds.seed_runner import seed_user, seed_themes, seed_templates

    await seed_user(db)
    theme_id_map = await seed_themes(db, force_rebuild=force_rebuild)
    await seed_templates(db, theme_id_map, force_rebuild=force_rebuild, skip_previews=skip_previews)

    return {
        "status": "ok",
        "themes_seeded": len(theme_id_map),
        "force_rebuild": force_rebuild,
        "skip_previews": skip_previews,
    }
