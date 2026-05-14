from __future__ import annotations

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class BrandKit(TimestampedModel):
    """Per-user brand defaults applied to new decks.

    Singleton-per-user enforced by a unique constraint on user_id at the
    application layer (every user has at most one brand kit).
    """
    __tablename__ = "brand_kits"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    primary_color: Mapped[str] = mapped_column(String(16), default="#1a1814")
    secondary_color: Mapped[str] = mapped_column(String(16), default="#f5f1eb")
    accent_color: Mapped[str] = mapped_column(String(16), default="#c89060")
    background_color: Mapped[str] = mapped_column(String(16), default="#ffffff")
    text_color: Mapped[str] = mapped_column(String(16), default="#1a1814")
    heading_font: Mapped[str] = mapped_column(String(64), default="Inter")
    body_font: Mapped[str] = mapped_column(String(64), default="Inter")
