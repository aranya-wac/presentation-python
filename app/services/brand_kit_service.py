from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand_kit import BrandKit
from app.models.user import User


async def get_kit(db: AsyncSession, user: User) -> BrandKit | None:
    return (
        await db.execute(select(BrandKit).where(BrandKit.user_id == str(user.id)))
    ).scalar_one_or_none()


async def upsert_kit(db: AsyncSession, user: User, payload: dict) -> BrandKit:
    kit = await get_kit(db, user)
    if not kit:
        kit = BrandKit(user_id=str(user.id))
        db.add(kit)

    # Only assign fields that were supplied — partial PATCH semantics.
    allowed = {
        "logo_url", "primary_color", "secondary_color", "accent_color",
        "background_color", "text_color", "heading_font", "body_font",
    }
    for key, value in payload.items():
        if key in allowed and value is not None:
            setattr(kit, key, value)

    await db.commit()
    await db.refresh(kit)
    return kit


def to_dict(kit: BrandKit | None) -> dict:
    if kit is None:
        return {
            "logo_url": "",
            "primary_color": "#1a1814",
            "secondary_color": "#f5f1eb",
            "accent_color": "#c89060",
            "background_color": "#ffffff",
            "text_color": "#1a1814",
            "heading_font": "Inter",
            "body_font": "Inter",
        }
    return {
        "logo_url": kit.logo_url,
        "primary_color": kit.primary_color,
        "secondary_color": kit.secondary_color,
        "accent_color": kit.accent_color,
        "background_color": kit.background_color,
        "text_color": kit.text_color,
        "heading_font": kit.heading_font,
        "body_font": kit.body_font,
    }
