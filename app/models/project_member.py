from __future__ import annotations

from sqlalchemy import String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class ProjectMember(TimestampedModel):
    """RBAC membership for a project.

    Roles (least → most privileged):
      - viewer: read-only — can view project, docs, generated decks.
      - editor: viewer + upload/delete docs + generate/regenerate/delete decks
                + edit project metadata.
      - owner:  editor + invite/remove members + change member roles
                + delete the project itself.

    A project always has at least one owner. The legacy `Project.owner_id`
    column is kept in sync (a member with role=owner exists) but membership
    is the canonical source of truth from now on.
    """
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # owner|editor|viewer
    invited_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
