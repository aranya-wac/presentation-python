from __future__ import annotations

from sqlalchemy import String, Boolean, Integer, JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class Presentation(TimestampedModel):
    __tablename__ = "presentations"

    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    template_id: Mapped[str] = mapped_column(String(36), ForeignKey("templates.id"), nullable=False)
    theme_id: Mapped[str] = mapped_column(String(36), ForeignKey("themes.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    slides: Mapped[list] = mapped_column(JSON, default=list)  # list of slide dicts
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Layouts saved within this deck — list of {id, name, blocks[]} where blocks
    # are placeholder-only versions of slide blocks (no user content).
    layouts: Mapped[list] = mapped_column(JSON, default=list)
