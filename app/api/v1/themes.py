from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.theme import Theme
from app.models.user import User

router = APIRouter(prefix="/themes", tags=["themes"])


class ThemeResponse(BaseModel):
    id: str
    name: str
    colors: dict
    fonts: dict


def _to_response(t: Theme) -> ThemeResponse:
    return ThemeResponse(id=str(t.id), name=t.name, colors=t.colors, fonts=t.fonts)


@router.get("", response_model=list[ThemeResponse])
async def list_themes(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ThemeResponse]:
    rows = (await db.execute(select(Theme))).scalars().all()
    return [_to_response(t) for t in rows]


@router.get("/{theme_id}", response_model=ThemeResponse)
async def get_theme(
    theme_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ThemeResponse:
    theme = (await db.execute(select(Theme).where(Theme.id == theme_id))).scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail=f"Theme {theme_id} not found")
    return _to_response(theme)
