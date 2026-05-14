from __future__ import annotations

from sqlalchemy import String, Boolean, JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class Template(TimestampedModel):
    """Hybrid template model. Two flavors coexist:

    - **rich** (slide_source='rich'): seeded built-in templates whose slides
      live in the JSON `slides` column with full block-level layout. These
      are `is_system=True, is_published=True`, no `created_by`.
    - **simple** (slide_source='simple'): user-created via the wizard.
      Their slide structure lives in the `template_slides` table — each
      row has just `title, layout_type, prompt_hint`. The JSON `slides`
      column stays empty for these. Generation populates a Presentation
      by walking the rows and asking AI per-slide.

    Visibility:
      is_system OR is_published → visible to all authenticated users
      else → only visible to `created_by` and platform admins
    """
    __tablename__ = "templates"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    theme_id: Mapped[str] = mapped_column(String(36), ForeignKey("themes.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False
    )  # {total_slides, estimated_duration, default_audience}
    slides: Mapped[list] = mapped_column(JSON, default=list)
    preview_pptx_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Discriminator + ownership (added in P1; idempotent migration backfills
    # rich seeded rows so they stay visible).
    slide_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="rich"
    )  # 'rich' | 'simple'
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    role: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("role_prompt_profiles.role"),
        nullable=True,
    )
