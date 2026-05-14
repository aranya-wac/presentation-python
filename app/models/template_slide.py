from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class TemplateSlide(TimestampedModel):
    """Per-slide structural row for `slide_source='simple'` templates.

    Compared to the rich block-level JSON in `templates.slides`, each row
    here carries just the bones the AI needs to fill in:
      - `title`: the human-facing slide title (also acts as a heading)
      - `layout_type`: one of {'title','bullets','image','columns'} — drives
        the expected output shape and the rendered block layout.
      - `prompt_hint`: free-text guidance passed to the AI per-slide.
    """
    __tablename__ = "template_slides"
    __table_args__ = (
        UniqueConstraint("template_id", "order", name="uq_template_slide_order"),
    )

    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    layout_type: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
