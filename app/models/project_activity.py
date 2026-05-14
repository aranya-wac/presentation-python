from __future__ import annotations

from sqlalchemy import String, JSON, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class ProjectActivity(TimestampedModel):
    """Append-only audit log for project lifecycle events."""
    __tablename__ = "project_activities"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    # Action vocabulary (extend with care, frontend renders by string match):
    #   project_created, project_updated, project_deleted,
    #   document_uploaded, document_deleted,
    #   extraction_completed, extraction_failed,
    #   presentation_generated, presentation_regenerated, presentation_deleted,
    #   member_added, member_removed, member_role_changed
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
