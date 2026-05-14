from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ProjectStatus = Literal["active", "draft", "archived"]
RoleType = Literal["developer", "ba", "sales", "pm", "qa"]
ExtractionStatus = Literal["pending", "complete", "failed"]


# ── Project ──────────────────────────────────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    tags: list[str] = []
    status: ProjectStatus = "active"


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[ProjectStatus] = None


class ProjectListItem(BaseModel):
    id: str
    name: str
    description: str
    status: ProjectStatus
    tags: list[str]
    owner_id: str
    document_count: int
    presentation_count: int
    created_at: str
    updated_at: str


class ProjectListPage(BaseModel):
    """Versioned paginated response — opt in via `?paginated=true`."""
    items: list[ProjectListItem]
    total: int
    limit: int
    offset: int


# ── Document ─────────────────────────────────────────────────────────────────

class DocumentListItem(BaseModel):
    id: str
    project_id: str
    filename: str
    original_filename: str
    format: str
    size_bytes: int
    version: int
    extraction_status: ExtractionStatus
    tags: list[str]
    uploaded_by: str
    created_at: str
    updated_at: str


class DocumentDetail(DocumentListItem):
    extracted_text: Optional[str] = None
    extraction_error: Optional[str] = None
    storage_path: str


class DocumentUpdateRequest(BaseModel):
    tags: Optional[list[str]] = None


# ── Generated presentation link ──────────────────────────────────────────────

class ProjectPresentationItem(BaseModel):
    id: str  # link id
    project_id: str
    presentation_id: str
    role: RoleType
    prompt_version: str
    source_document_ids: list[str]
    generated_by: str
    title: str
    slide_count: int
    created_at: str
    prior_link_id: Optional[str] = None
    version: int = 1


class RegenerateRequest(BaseModel):
    """All fields optional — defaults pulled from the prior link."""
    document_ids: Optional[list[str]] = None
    template_id: Optional[str] = None
    theme_id: Optional[str] = None
    title: Optional[str] = None
    role: Optional[RoleType] = None  # allows changing role on regen


# ── Project detail (composite) ───────────────────────────────────────────────

class ProjectDetail(ProjectListItem):
    documents: list[DocumentListItem] = []
    presentations: list[ProjectPresentationItem] = []


# ── Role-based generation ────────────────────────────────────────────────────

class GenerateFromProjectRequest(BaseModel):
    role: RoleType
    document_ids: Optional[list[str]] = None  # default: all extraction-complete docs
    template_id: Optional[str] = None
    theme_id: Optional[str] = None
    title: Optional[str] = None
