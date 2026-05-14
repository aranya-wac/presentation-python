from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import brand_kit_service

router = APIRouter(prefix="/brand-kit", tags=["brand-kit"])


class BrandKitPayload(BaseModel):
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    accent_color: str | None = None
    background_color: str | None = None
    text_color: str | None = None
    heading_font: str | None = None
    body_font: str | None = None


class BrandKitResponse(BaseModel):
    logo_url: str
    primary_color: str
    secondary_color: str
    accent_color: str
    background_color: str
    text_color: str
    heading_font: str
    body_font: str


@router.get("", response_model=BrandKitResponse)
async def get_brand_kit(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrandKitResponse:
    kit = await brand_kit_service.get_kit(db, current_user)
    return BrandKitResponse(**brand_kit_service.to_dict(kit))


@router.put("", response_model=BrandKitResponse)
async def update_brand_kit(
    payload: BrandKitPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrandKitResponse:
    kit = await brand_kit_service.upsert_kit(
        db, current_user, payload.model_dump(exclude_none=True)
    )
    return BrandKitResponse(**brand_kit_service.to_dict(kit))
