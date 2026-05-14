from __future__ import annotations

from sqlalchemy import String, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class PresentationVersion(TimestampedModel):
    """Snapshot of a presentation at a point in time.

    Captured opportunistically (debounced server-side, see version_service.py)
    so the user can roll back without polluting history with every keystroke.
    """
    __tablename__ = "presentation_versions"

    presentation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("presentations.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="")
    slides: Mapped[list] = mapped_column(JSON, default=list)
    theme_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(255), default="")
