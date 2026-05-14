from __future__ import annotations

from sqlalchemy import Integer, JSON, String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class Project(TimestampedModel):
    __tablename__ = "projects"

    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|draft|archived
    tags: Mapped[list] = mapped_column(JSON, default=list)


class ProjectDocument(TimestampedModel):
    __tablename__ = "project_documents"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    uploaded_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf|docx|txt
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    extraction_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending|complete|failed
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class ProjectPresentationLink(TimestampedModel):
    """Links a generated Presentation to a Project with role + provenance metadata.

    `prior_link_id` chains regenerations: each regen of a deck creates a NEW
    link pointing back at the link it was regenerated from, so history is
    preserved (the prior deck stays accessible). `version` is 1 for the
    original generation, +1 per regen step.
    """
    __tablename__ = "project_presentation_links"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    presentation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("presentations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # developer|ba|sales|pm|qa
    source_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    generated_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    prior_link_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("project_presentation_links.id"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
