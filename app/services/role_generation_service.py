from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini_client import generate_json, get_last_token_count
from app.core.exceptions import GeminiError, NotFoundError, ValidationError
from app.models.presentation import Presentation
from app.models.project import Project, ProjectDocument, ProjectPresentationLink
from app.models.role_prompt import RolePromptProfile
from app.models.template import Template
from app.models.theme import Theme
from app.models.user import User
from app.schemas.project import (
    GenerateFromProjectRequest,
    ProjectPresentationItem,
    RoleType,
)
from app.services.project_service import get_project_for_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "v2"

# Templates over this slide threshold get chunked into multiple Gemini calls
# so we don't blow the 4096-token output ceiling on a single generation.
SLIDES_PER_CHUNK = 6

_PLACEHOLDER_RE = re.compile(r"\[PLACEHOLDER[^\]]*\]", re.IGNORECASE)
_TEXT_BLOCK_TYPES = {
    "title", "subtitle", "heading", "caption", "text", "bullet", "bullets",
    "body", "quote", "stat", "stats", "swot", "persona", "label",
}

# Hardcoded defaults — seeded into `role_prompt_profiles` on first startup.
# At runtime, the service reads from the DB (see `_load_profile`) so admins
# can override `audience`/`focus`/`prompt_template` without redeploying.
DEFAULT_ROLE_PROFILES: dict[str, dict[str, str]] = {
    "developer": {
        "audience": "Engineering / development team",
        "focus": (
            "Architecture, technical specifications, API contracts, data models, "
            "implementation tasks, libraries/frameworks, performance considerations, "
            "and engineering risks. Use precise technical language."
        ),
    },
    "ba": {
        "audience": "Business analysts and product stakeholders",
        "focus": (
            "Business requirements, user stories with acceptance criteria, "
            "process flows, stakeholder needs, success metrics, and assumptions. "
            "Frame everything in terms of business value."
        ),
    },
    "sales": {
        "audience": "Prospective customers and sales prospects",
        "focus": (
            "Value proposition, market positioning, customer pain points solved, "
            "differentiators, pricing/ROI, social proof, and a clear call-to-action. "
            "Use persuasive, benefit-led language."
        ),
    },
    "pm": {
        "audience": "Project managers and project sponsors",
        "focus": (
            "Scope, timeline, milestones, deliverables, resource plan, "
            "dependencies, risks with mitigations, and status. "
            "Be concrete and time-boxed."
        ),
    },
    "qa": {
        "audience": "Quality assurance and test engineering team",
        "focus": (
            "Test scope, test strategy, test cases, coverage matrix, "
            "defect categories, environments, and entry/exit criteria. "
            "Be systematic and traceability-oriented."
        ),
    },
}


# Backward-compat alias. Existing call sites read via _load_profile/_load_all.
ROLE_PROFILES = DEFAULT_ROLE_PROFILES


async def seed_role_profiles(db: AsyncSession) -> None:
    """Insert default profile rows for any role that doesn't yet have one.

    Existing rows are NEVER overwritten — admin edits stick across deploys.
    """
    existing = (
        await db.execute(select(RolePromptProfile.role))
    ).scalars().all()
    existing_set = set(existing)
    inserted = 0
    for role, p in DEFAULT_ROLE_PROFILES.items():
        if role in existing_set:
            continue
        db.add(
            RolePromptProfile(
                role=role,
                audience=p["audience"],
                focus=p["focus"],
                prompt_template=None,
            )
        )
        inserted += 1
    if inserted:
        await db.commit()
        logger.info(f"Seeded {inserted} role prompt profile(s)")


async def _load_profile(db: AsyncSession, role: str) -> dict[str, str]:
    """Load a role profile from the DB; fall back to hardcoded defaults if
    the row hasn't been seeded yet (e.g. first request after a fresh install).
    """
    row = (
        await db.execute(
            select(RolePromptProfile).where(RolePromptProfile.role == role)
        )
    ).scalar_one_or_none()
    if row:
        return {
            "audience": row.audience,
            "focus": row.focus,
            "prompt_template": row.prompt_template or "",
        }
    fallback = DEFAULT_ROLE_PROFILES.get(role)
    if not fallback:
        raise ValidationError(f"Unsupported role '{role}'")
    return {**fallback, "prompt_template": ""}


async def _load_all_profiles(db: AsyncSession) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            select(RolePromptProfile).order_by(RolePromptProfile.role)
        )
    ).scalars().all()
    if rows:
        return [
            {
                "role": r.role,
                "audience": r.audience,
                "focus": r.focus,
            }
            for r in rows
        ]
    return [
        {"role": role, "audience": p["audience"], "focus": p["focus"]}
        for role, p in DEFAULT_ROLE_PROFILES.items()
    ]


def _block_has_placeholder(block: dict) -> bool:
    content = block.get("content", "")
    return isinstance(content, str) and bool(_PLACEHOLDER_RE.search(content))


def _slides_skeleton(template: Template) -> list[dict[str, Any]]:
    return copy.deepcopy(template.slides or [])


def _collect_slots(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for slide in slides:
        slide_type = slide.get("type", "content")
        for block in slide.get("blocks", []):
            btype = (block.get("type") or "").lower()
            content = block.get("content", "")
            if btype == "image":
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if btype and btype not in _TEXT_BLOCK_TYPES and not _block_has_placeholder(block):
                continue
            slots.append(
                {
                    "id": block.get("id", ""),
                    "slide_order": slide.get("order", 0),
                    "slide_type": slide_type,
                    "block_type": block.get("type", "text"),
                    "current": content,
                }
            )
    return slots


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def _build_corpus(documents: list[ProjectDocument], per_doc_limit: int = 6000) -> str:
    parts: list[str] = []
    for d in documents:
        text = (d.extracted_text or "").strip()
        if not text:
            continue
        parts.append(
            f"=== Document: {d.original_filename} (v{d.version}, {d.format}) ===\n"
            + _truncate(text, per_doc_limit)
        )
    return "\n\n".join(parts)


def _build_prompt(
    role: RoleType,
    project: Project,
    template: Template,
    corpus: str,
    slots: list[dict[str, Any]],
    chunk_label: str | None = None,
    profile: dict[str, str] | None = None,
) -> str:
    if profile is None:
        profile = DEFAULT_ROLE_PROFILES[role]
    chunk_note = (
        f"\nNOTE: You are rewriting {chunk_label}. Stay consistent with the "
        f"overall narrative — assume earlier and later slides exist and "
        f"will be filled in separately.\n"
        if chunk_label
        else ""
    )
    return f"""You are generating a {role.upper()} role-specific presentation from a project's source documents.

PROJECT
Name: {project.name}
Description: {project.description or '(no description)'}

ROLE: {role.upper()}
Target audience: {profile['audience']}
Editorial focus: {profile['focus']}

TEMPLATE: {template.name}
Template description: {template.description or ''}
{chunk_note}
SOURCE DOCUMENTS (extracted text):
{corpus or '(no extracted text available)'}

You are given a JSON list of text blocks from the template. Each block has:
- "id": block id (echo this exact id in your response)
- "slide_order": which slide it lives on (1-indexed)
- "slide_type": the slide's role (title, agenda, content, stats, closing, etc.)
- "block_type": the block role (title, subtitle, heading, bullets, stat, caption, etc.)
- "current": the existing placeholder text in that block

Rewrite the "current" text of every block so the deck delivers the project's
content from the {role.upper()} role's perspective. Hard rules:
- Ground all claims in the SOURCE DOCUMENTS. Do not invent facts that contradict
  the documents. If the documents are silent on a point, write a plausible
  generic placeholder consistent with the role's focus.
- Keep approximately the same length, line count, and structure as the current text.
- If the current text uses bullets / multiple lines / labels (e.g. "STRENGTHS\\n..."),
  keep that exact shape — same number of bullets, same labels.
- Replace any bracketed placeholder markers ([PLACEHOLDER: ...], [LOGO_PLACEHOLDER],
  [Name], etc.) with concrete content. Do NOT keep brackets in the output.
- Do NOT add or remove blocks. Do NOT change slide order.

Blocks:
{json.dumps(slots, ensure_ascii=False)}

Return a JSON object with a single key "replacements" mapping block id to the new
text. Every input id must appear in the output. Example:
{{"replacements": {{"s1-title": "...", "s1-subtitle": "..."}}}}

Return ONLY valid JSON. No markdown fences, no commentary."""


async def _generate_replacements_chunked(
    role: RoleType,
    project: Project,
    template: Template,
    corpus: str,
    slides: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    profile: dict[str, str],
) -> tuple[dict[str, Any], int]:
    """Group slots by slide and chunk into Gemini calls of ~SLIDES_PER_CHUNK
    slides each. Each call shares the same project/role/corpus context so the
    deck stays coherent. Replacements from all calls are merged."""
    slide_count = len(slides)
    if slide_count <= SLIDES_PER_CHUNK:
        prompt = _build_prompt(role, project, template, corpus, slots, profile=profile)
        try:
            ai_result = await generate_json(prompt)
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(f"Role generation failed: {exc}") from exc
        token_count = get_last_token_count()
        replacements = (
            ai_result.get("replacements", {}) if isinstance(ai_result, dict) else {}
        )
        return (replacements if isinstance(replacements, dict) else {}), token_count

    # Group slots by slide_order so slides aren't split across chunks
    by_slide: dict[int, list[dict[str, Any]]] = {}
    for slot in slots:
        by_slide.setdefault(slot["slide_order"], []).append(slot)
    ordered_slides = sorted(by_slide.keys())

    merged: dict[str, Any] = {}
    total_tokens = 0
    for chunk_start in range(0, len(ordered_slides), SLIDES_PER_CHUNK):
        chunk_orders = ordered_slides[chunk_start : chunk_start + SLIDES_PER_CHUNK]
        chunk_slots: list[dict[str, Any]] = []
        for o in chunk_orders:
            chunk_slots.extend(by_slide[o])
        if not chunk_slots:
            continue
        chunk_label = (
            f"slides {chunk_orders[0]}–{chunk_orders[-1]} of {slide_count}"
        )
        prompt = _build_prompt(
            role, project, template, corpus, chunk_slots,
            chunk_label=chunk_label, profile=profile,
        )
        try:
            ai_result = await generate_json(prompt)
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(
                f"Role generation failed on {chunk_label}: {exc}"
            ) from exc
        total_tokens += get_last_token_count()
        chunk_replacements = (
            ai_result.get("replacements", {}) if isinstance(ai_result, dict) else {}
        )
        if isinstance(chunk_replacements, dict):
            merged.update(chunk_replacements)
    return merged, total_tokens


def _apply_replacements(
    slides: list[dict[str, Any]], replacements: dict[str, Any]
) -> None:
    for slide in slides:
        for block in slide.get("blocks", []):
            bid = block.get("id", "")
            if bid in replacements and isinstance(replacements[bid], str):
                block["content"] = replacements[bid]
            content = block.get("content", "")
            if isinstance(content, str) and "[PLACEHOLDER" in content.upper():
                block["content"] = _PLACEHOLDER_RE.sub("", content).strip()


async def _resolve_template_and_theme(
    db: AsyncSession,
    template_id: Optional[str],
    theme_id: Optional[str],
) -> tuple[Template, Theme]:
    if template_id:
        template = (
            await db.execute(select(Template).where(Template.id == template_id))
        ).scalar_one_or_none()
        if not template:
            raise NotFoundError(f"Template {template_id} not found")
    else:
        template = (
            await db.execute(
                select(Template).where(Template.is_active == True)  # noqa: E712
            )
        ).scalars().first()
        if not template:
            raise NotFoundError("No active templates available")

    resolved_theme_id = theme_id or template.theme_id
    theme = (
        await db.execute(select(Theme).where(Theme.id == resolved_theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise NotFoundError(f"Theme {resolved_theme_id} not found")
    return template, theme


async def _resolve_documents(
    db: AsyncSession, project_id: str, document_ids: Optional[list[str]]
) -> list[ProjectDocument]:
    stmt = select(ProjectDocument).where(
        ProjectDocument.project_id == project_id,
        ProjectDocument.extraction_status == "complete",
    )
    if document_ids:
        stmt = stmt.where(ProjectDocument.id.in_(document_ids))

    docs = (await db.execute(stmt)).scalars().all()
    if not docs:
        # Differentiate between "project has nothing usable" and "all explicit ids are bad"
        any_doc = (
            await db.execute(
                select(ProjectDocument).where(ProjectDocument.project_id == project_id)
            )
        ).scalars().first()
        if not any_doc:
            raise ValidationError(
                "Project has no documents available for generation."
            )
        raise ValidationError(
            "No usable document content; ensure documents have completed extraction."
        )
    return list(docs)


# ── Public API ───────────────────────────────────────────────────────────────

async def generate_role_presentation(
    db: AsyncSession,
    user: User,
    project_id: str,
    req: GenerateFromProjectRequest,
) -> ProjectPresentationItem:
    project = await get_project_for_user(db, user, project_id, min_role="editor")

    profile = await _load_profile(db, req.role)

    documents = await _resolve_documents(db, project_id, req.document_ids)
    template, theme = await _resolve_template_and_theme(db, req.template_id, req.theme_id)

    slides = _slides_skeleton(template)
    if not slides:
        raise ValidationError(
            f"Template '{template.name}' has no slides to populate."
        )

    slots = _collect_slots(slides)
    corpus = _build_corpus(documents)

    if slots:
        replacements, token_count = await _generate_replacements_chunked(
            req.role, project, template, corpus, slides, slots, profile,
        )
        _apply_replacements(slides, replacements)
    else:
        token_count = 0

    title = (
        (req.title or "").strip()
        or f"{project.name} — {req.role.upper()} Deck"
    )

    presentation = Presentation(
        user_id=str(user.id),
        template_id=str(template.id),
        theme_id=str(theme.id),
        title=title,
        description=f"Auto-generated {req.role.upper()} deck for project '{project.name}'.",
        logo_url="",
        slides=slides,
        is_preview=False,
        token_count=token_count,
    )
    db.add(presentation)
    await db.flush()

    link = ProjectPresentationLink(
        project_id=str(project.id),
        presentation_id=str(presentation.id),
        role=req.role,
        source_document_ids=[str(d.id) for d in documents],
        prompt_version=PROMPT_VERSION,
        generated_by=str(user.id),
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    await db.refresh(presentation)

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=str(project.id),
        actor_id=str(user.id),
        action="presentation_generated",
        entity_type="presentation",
        entity_id=str(presentation.id),
        summary=f"Generated {req.role.upper()} deck '{presentation.title}'",
        metadata={
            "role": req.role,
            "slide_count": len(presentation.slides) if presentation.slides else 0,
            "token_count": token_count,
            "source_document_count": len(documents),
        },
    )

    return ProjectPresentationItem(
        id=str(link.id),
        project_id=str(link.project_id),
        presentation_id=str(link.presentation_id),
        role=link.role,
        prompt_version=link.prompt_version,
        source_document_ids=link.source_document_ids or [],
        generated_by=str(link.generated_by),
        title=presentation.title,
        slide_count=len(presentation.slides) if presentation.slides else 0,
        created_at=link.created_at.isoformat() if link.created_at else "",
    )


async def list_supported_roles(db: AsyncSession) -> list[dict[str, str]]:
    return await _load_all_profiles(db)


async def delete_project_presentation(
    db: AsyncSession,
    user: User,
    project_id: str,
    link_id: str,
) -> None:
    """Delete a project-deck: removes the link AND the underlying presentation.

    Per product decision: a generated deck "belongs" to the project, so
    deleting from the project view should not leave orphans in /decks.
    """
    from app.models.presentation import Presentation
    from app.services import activity_service

    project = await get_project_for_user(db, user, project_id, min_role="editor")

    link = (
        await db.execute(
            select(ProjectPresentationLink).where(
                ProjectPresentationLink.id == link_id,
                ProjectPresentationLink.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not link:
        raise NotFoundError(f"Link {link_id} not found")

    presentation_id = str(link.presentation_id)

    # Detach any newer regenerations that point at this link so we don't
    # leave dangling FKs.
    await db.execute(
        ProjectPresentationLink.__table__.update()
        .where(ProjectPresentationLink.prior_link_id == link.id)
        .values(prior_link_id=None)
    )

    title_for_log = ""
    presentation = (
        await db.execute(
            select(Presentation).where(Presentation.id == presentation_id)
        )
    ).scalar_one_or_none()
    if presentation:
        title_for_log = presentation.title

    # Delete the link first so the FK from project_presentation_links →
    # presentations releases before we drop the presentation row.
    await db.delete(link)
    await db.flush()
    if presentation:
        await db.delete(presentation)
    await db.commit()

    await activity_service.record(
        db,
        project_id=str(project.id),
        actor_id=str(user.id),
        action="presentation_deleted",
        entity_type="presentation",
        entity_id=presentation_id,
        summary=f"Deleted deck '{title_for_log}'" if title_for_log else "Deleted deck",
    )


async def regenerate_project_presentation(
    db: AsyncSession,
    user: User,
    project_id: str,
    link_id: str,
    overrides: dict,
) -> ProjectPresentationItem:
    """Regenerate a deck — keeps history by creating a NEW link + presentation
    that point back at the prior link via `prior_link_id`. The prior deck is
    untouched. Version is bumped by one."""
    project = await get_project_for_user(db, user, project_id, min_role="editor")

    prior = (
        await db.execute(
            select(ProjectPresentationLink).where(
                ProjectPresentationLink.id == link_id,
                ProjectPresentationLink.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not prior:
        raise NotFoundError(f"Link {link_id} not found")

    # Resolve effective inputs: overrides win, else fall back to prior link.
    role = overrides.get("role") or prior.role
    document_ids = overrides.get("document_ids") or list(prior.source_document_ids or [])
    template_id = overrides.get("template_id")
    theme_id = overrides.get("theme_id")
    title_override = overrides.get("title")

    req = GenerateFromProjectRequest(
        role=role,
        document_ids=document_ids,
        template_id=template_id,
        theme_id=theme_id,
        title=title_override,
    )

    item = await generate_role_presentation(db, user, project_id, req)

    # Wire history: refetch the new link, set prior_link_id + bump version.
    new_link = (
        await db.execute(
            select(ProjectPresentationLink).where(
                ProjectPresentationLink.id == item.id
            )
        )
    ).scalar_one()
    new_link.prior_link_id = str(prior.id)
    new_link.version = (getattr(prior, "version", 1) or 1) + 1
    await db.commit()
    await db.refresh(new_link)

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=str(project.id),
        actor_id=str(user.id),
        action="presentation_regenerated",
        entity_type="presentation",
        entity_id=str(new_link.presentation_id),
        summary=f"Regenerated {role.upper()} deck (v{new_link.version})",
        metadata={
            "prior_link_id": str(prior.id),
            "version": new_link.version,
            "role": role,
        },
    )

    item.prior_link_id = str(prior.id)
    item.version = new_link.version
    return item
