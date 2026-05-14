from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


SlideLayoutType = Literal["title", "bullets", "image", "columns"]
SlideSource = Literal["rich", "simple"]


class TemplateMetadataSchema(BaseModel):
    total_slides: int
    estimated_duration: int
    default_audience: str


class TemplateListItem(BaseModel):
    id: str
    name: str
    description: str
    category: str
    tags: list[str]
    thumbnail_url: str
    theme_id: str
    is_active: bool
    metadata: TemplateMetadataSchema
    preview_slide: Optional[dict] = None
    theme: Optional[dict] = None
    # New fields (P-Templates)
    slide_source: SlideSource = "rich"
    is_system: bool = False
    is_published: bool = True
    created_by: Optional[str] = None
    role: Optional[str] = None


class TemplateDetail(TemplateListItem):
    slides: list[dict]
    preview_presentation_id: Optional[str] = None
    # For 'simple' templates, the slide blueprint rows
    template_slides: list[dict] = []


# ── Wizard create/update ─────────────────────────────────────────────────────

class TemplateSlideInput(BaseModel):
    order: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    layout_type: SlideLayoutType
    prompt_hint: str = ""


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    category: str = ""
    tags: list[str] = []
    role: Optional[str] = None  # FK to role_prompt_profiles.role
    theme_id: str
    slides: list[TemplateSlideInput]


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    role: Optional[str] = None  # use empty-string sentinel to clear (handled in service)
    theme_id: Optional[str] = None
    slides: Optional[list[TemplateSlideInput]] = None


class TemplatePublishRequest(BaseModel):
    is_published: bool


# ── Generation from a 'simple' template ──────────────────────────────────────

class GenerateFromSimpleTemplateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    title: Optional[str] = None


# ── Existing schemas ─────────────────────────────────────────────────────────

class TemplateFilter(BaseModel):
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: bool = True


class PreviewResponse(BaseModel):
    slides: list[dict]
    theme: dict
    token_count: int = 0
