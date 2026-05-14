"""User-driven template authoring (slide_source='simple').

This module is the writeable surface for templates. The existing
`template_service` handles read paths for the listing/detail screens —
they share the same table but separate concerns keep changes scoped.

Permission model:
- Any authenticated user can POST → creates `is_system=false,
  is_published=false, created_by=self`.
- Only `created_by` (or a platform admin) can PUT/DELETE.
- Only platform admins can flip `is_published` (the publish endpoint).
- `is_system=true` rows (seeded built-ins) are read-only — PUT/DELETE 403.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.role_prompt import RolePromptProfile
from app.models.template import Template
from app.models.template_slide import TemplateSlide
from app.models.theme import Theme
from app.models.user import User
from app.schemas.template import (
    TemplateCreateRequest,
    TemplateDetail,
    TemplateMetadataSchema,
    TemplateUpdateRequest,
)


def _is_admin(user: User) -> bool:
    return getattr(user, "role", None) == "admin"


def _to_template_slide_dict(s: TemplateSlide) -> dict:
    return {
        "id": str(s.id),
        "order": s.order,
        "title": s.title,
        "layout_type": s.layout_type,
        "prompt_hint": s.prompt_hint,
    }


async def _validate_role(db: AsyncSession, role: Optional[str]) -> Optional[str]:
    """Verify role exists in role_prompt_profiles. Empty/None → no role."""
    if role is None or role == "":
        return None
    exists = (
        await db.execute(
            select(RolePromptProfile.role).where(RolePromptProfile.role == role)
        )
    ).scalar_one_or_none()
    if not exists:
        raise ValidationError(
            f"Unknown role '{role}'. Must match a role_prompt_profiles row."
        )
    return role


async def _validate_theme(db: AsyncSession, theme_id: str) -> Theme:
    theme = (
        await db.execute(select(Theme).where(Theme.id == theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise ValidationError(f"Theme {theme_id} not found")
    return theme


def _validate_slides(slides) -> None:
    if not slides:
        raise ValidationError("A template needs at least one slide.")
    orders = [s.order for s in slides]
    if len(set(orders)) != len(orders):
        raise ValidationError("Slide `order` values must be unique within a template.")


async def _detail_with_slides(db: AsyncSession, template: Template) -> TemplateDetail:
    rows = (
        await db.execute(
            select(TemplateSlide)
            .where(TemplateSlide.template_id == template.id)
            .order_by(TemplateSlide.order)
        )
    ).scalars().all()
    meta = template.metadata_json or {}
    return TemplateDetail(
        id=str(template.id),
        name=template.name,
        description=template.description,
        category=template.category,
        tags=template.tags or [],
        thumbnail_url=template.thumbnail_url or "",
        theme_id=str(template.theme_id),
        is_active=template.is_active,
        metadata=TemplateMetadataSchema(
            total_slides=meta.get("total_slides", len(rows)),
            estimated_duration=meta.get("estimated_duration", 0),
            default_audience=meta.get("default_audience", ""),
        ),
        slides=template.slides or [],
        slide_source=template.slide_source,
        is_system=template.is_system,
        is_published=template.is_published,
        created_by=str(template.created_by) if template.created_by else None,
        role=template.role,
        template_slides=[_to_template_slide_dict(r) for r in rows],
    )


# ── Create ───────────────────────────────────────────────────────────────────

async def create_template(
    db: AsyncSession, user: User, req: TemplateCreateRequest
) -> TemplateDetail:
    _validate_slides(req.slides)
    await _validate_theme(db, req.theme_id)
    role = await _validate_role(db, req.role)

    template = Template(
        name=req.name,
        description=req.description,
        category=req.category,
        tags=req.tags or [],
        thumbnail_url="",
        theme_id=req.theme_id,
        is_active=True,
        metadata_json={
            "total_slides": len(req.slides),
            "estimated_duration": max(1, len(req.slides) * 2),
            "default_audience": "",
        },
        slides=[],  # 'simple' templates leave the rich JSON empty
        preview_pptx_path=None,
        slide_source="simple",
        is_system=False,
        is_published=False,
        created_by=str(user.id),
        role=role,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationError(
            f"Template name '{req.name}' is already taken."
        ) from exc

    for s in req.slides:
        db.add(
            TemplateSlide(
                template_id=str(template.id),
                order=s.order,
                title=s.title,
                layout_type=s.layout_type,
                prompt_hint=s.prompt_hint,
            )
        )
    await db.commit()
    await db.refresh(template)
    return await _detail_with_slides(db, template)


# ── Update ───────────────────────────────────────────────────────────────────

async def _load_writable(
    db: AsyncSession, user: User, template_id: str
) -> Template:
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise NotFoundError(f"Template {template_id} not found")
    if template.is_system:
        raise ForbiddenError("Built-in templates are read-only.")
    if template.slide_source != "simple":
        raise ForbiddenError(
            "This template was not created via the wizard and cannot be edited here."
        )
    if str(template.created_by) != str(user.id) and not _is_admin(user):
        raise ForbiddenError("You can only edit templates you created.")
    return template


async def update_template(
    db: AsyncSession,
    user: User,
    template_id: str,
    req: TemplateUpdateRequest,
) -> TemplateDetail:
    template = await _load_writable(db, user, template_id)

    if req.name is not None:
        template.name = req.name
    if req.description is not None:
        template.description = req.description
    if req.category is not None:
        template.category = req.category
    if req.tags is not None:
        template.tags = req.tags
    if req.theme_id is not None:
        await _validate_theme(db, req.theme_id)
        template.theme_id = req.theme_id
    if req.role is not None:
        # Empty string clears the role; otherwise validate.
        template.role = await _validate_role(db, req.role) if req.role else None

    if req.slides is not None:
        _validate_slides(req.slides)
        # Replace-in-place: drop existing rows, insert new ones.
        await db.execute(
            delete(TemplateSlide).where(TemplateSlide.template_id == template.id)
        )
        for s in req.slides:
            db.add(
                TemplateSlide(
                    template_id=str(template.id),
                    order=s.order,
                    title=s.title,
                    layout_type=s.layout_type,
                    prompt_hint=s.prompt_hint,
                )
            )
        meta = dict(template.metadata_json or {})
        meta["total_slides"] = len(req.slides)
        meta["estimated_duration"] = max(1, len(req.slides) * 2)
        template.metadata_json = meta

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationError(f"Update failed: {exc}") from exc
    await db.refresh(template)
    return await _detail_with_slides(db, template)


# ── Delete ───────────────────────────────────────────────────────────────────

async def delete_template(
    db: AsyncSession, user: User, template_id: str
) -> None:
    template = await _load_writable(db, user, template_id)
    await db.execute(
        delete(TemplateSlide).where(TemplateSlide.template_id == template.id)
    )
    await db.delete(template)
    await db.commit()


# ── Publish (admin-only) ─────────────────────────────────────────────────────

async def set_published(
    db: AsyncSession, user: User, template_id: str, is_published: bool
) -> TemplateDetail:
    if not _is_admin(user):
        raise ForbiddenError("Publishing is restricted to admins.")
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise NotFoundError(f"Template {template_id} not found")
    template.is_published = is_published
    await db.commit()
    await db.refresh(template)
    return await _detail_with_slides(db, template)


# ── Detail (visibility-aware) ────────────────────────────────────────────────

async def get_template_detail(
    db: AsyncSession, user: User, template_id: str
) -> TemplateDetail:
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template:
        raise NotFoundError(f"Template {template_id} not found")
    visible = (
        template.is_system
        or template.is_published
        or str(template.created_by) == str(user.id)
        or _is_admin(user)
    )
    if not visible:
        raise NotFoundError(f"Template {template_id} not found")
    return await _detail_with_slides(db, template)
