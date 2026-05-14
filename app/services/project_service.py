from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.presentation import Presentation
from app.models.project import Project, ProjectDocument, ProjectPresentationLink
from app.models.user import User
from app.schemas.project import (
    DocumentListItem,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectListItem,
    ProjectPresentationItem,
    ProjectUpdateRequest,
)


def _ts(dt) -> str:
    return dt.isoformat() if dt else ""


def _to_list_item(p: Project, doc_count: int, pres_count: int) -> ProjectListItem:
    return ProjectListItem(
        id=str(p.id),
        name=p.name,
        description=p.description,
        status=p.status,
        tags=p.tags or [],
        owner_id=str(p.owner_id),
        document_count=doc_count,
        presentation_count=pres_count,
        created_at=_ts(p.created_at),
        updated_at=_ts(p.updated_at),
    )


def _doc_to_item(d: ProjectDocument) -> DocumentListItem:
    return DocumentListItem(
        id=str(d.id),
        project_id=str(d.project_id),
        filename=d.filename,
        original_filename=d.original_filename,
        format=d.format,
        size_bytes=d.size_bytes,
        version=d.version,
        extraction_status=d.extraction_status,
        tags=d.tags or [],
        uploaded_by=str(d.uploaded_by),
        created_at=_ts(d.created_at),
        updated_at=_ts(d.updated_at),
    )


def _link_to_item(
    link: ProjectPresentationLink, presentation: Optional[Presentation]
) -> ProjectPresentationItem:
    return ProjectPresentationItem(
        id=str(link.id),
        project_id=str(link.project_id),
        presentation_id=str(link.presentation_id),
        role=link.role,
        prompt_version=link.prompt_version,
        source_document_ids=link.source_document_ids or [],
        generated_by=str(link.generated_by),
        title=presentation.title if presentation else "",
        slide_count=len(presentation.slides) if presentation and presentation.slides else 0,
        created_at=_ts(link.created_at),
        prior_link_id=str(link.prior_link_id) if link.prior_link_id else None,
        version=getattr(link, "version", 1) or 1,
    )


async def get_project_for_user(
    db: AsyncSession, user: User, project_id: str, *, min_role: str = "viewer"
) -> Project:
    """Membership-gated project fetch. Defaults to viewer access — callers
    that need write access pass `min_role='editor'` (or 'owner' for member
    management / project deletion)."""
    from app.services.members_service import require_membership

    await require_membership(db, user, project_id, min_role=min_role)
    p = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if not p:
        raise NotFoundError(f"Project {project_id} not found")
    return p


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def create_project(
    db: AsyncSession, user: User, req: ProjectCreateRequest
) -> ProjectListItem:
    from app.models.project_member import ProjectMember
    from app.services import activity_service

    project = Project(
        owner_id=str(user.id),
        name=req.name,
        description=req.description,
        status=req.status,
        tags=req.tags or [],
    )
    db.add(project)
    await db.flush()  # need project.id to attach the membership row

    # Creator becomes owner via membership (canonical source of truth)
    db.add(
        ProjectMember(
            project_id=str(project.id),
            user_id=str(user.id),
            role="owner",
            invited_by=str(user.id),
        )
    )
    await db.commit()
    await db.refresh(project)

    await activity_service.record(
        db,
        project_id=str(project.id),
        actor_id=str(user.id),
        action="project_created",
        entity_type="project",
        entity_id=str(project.id),
        summary=f"Created project '{project.name}'",
    )
    return _to_list_item(project, 0, 0)


async def list_projects(
    db: AsyncSession,
    user: User,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "updated_desc",
    limit: Optional[int] = None,
    offset: int = 0,
    membership_filter: Optional[str] = None,  # 'owned' | 'shared' | None=all
) -> list[ProjectListItem]:
    from app.models.project_member import ProjectMember

    member_subquery = select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id
    )
    if membership_filter == "owned":
        member_subquery = member_subquery.where(ProjectMember.role == "owner")
    base = select(Project).where(Project.id.in_(member_subquery))
    if membership_filter == "shared":
        # Member but NOT owner — i.e. someone else gave you access
        base = base.where(
            Project.id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == user.id,
                    ProjectMember.role.in_(["editor", "viewer"]),
                )
            )
        )
    if status:
        base = base.where(Project.status == status)
    if search:
        like = f"%{search}%"
        base = base.where(
            (Project.name.ilike(like)) | (Project.description.ilike(like))
        )

    sort_map = {
        "updated_desc": Project.updated_at.desc(),
        "updated_asc": Project.updated_at.asc(),
        "created_desc": Project.created_at.desc(),
        "created_asc": Project.created_at.asc(),
        "name_asc": Project.name.asc(),
        "name_desc": Project.name.desc(),
    }
    stmt = base.order_by(sort_map.get(sort, Project.updated_at.desc()))
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)

    projects = (await db.execute(stmt)).scalars().all()
    if not projects:
        return []

    pids = [str(p.id) for p in projects]
    doc_rows = (
        await db.execute(
            select(ProjectDocument.project_id, func.count(ProjectDocument.id))
            .where(ProjectDocument.project_id.in_(pids))
            .group_by(ProjectDocument.project_id)
        )
    ).all()
    pres_rows = (
        await db.execute(
            select(
                ProjectPresentationLink.project_id,
                func.count(ProjectPresentationLink.id),
            )
            .where(ProjectPresentationLink.project_id.in_(pids))
            .group_by(ProjectPresentationLink.project_id)
        )
    ).all()
    doc_map = {pid: cnt for pid, cnt in doc_rows}
    pres_map = {pid: cnt for pid, cnt in pres_rows}

    return [
        _to_list_item(p, doc_map.get(str(p.id), 0), pres_map.get(str(p.id), 0))
        for p in projects
    ]


async def count_projects(
    db: AsyncSession,
    user: User,
    status: Optional[str] = None,
    search: Optional[str] = None,
    membership_filter: Optional[str] = None,
) -> int:
    from app.models.project_member import ProjectMember

    member_subquery = select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id
    )
    if membership_filter == "owned":
        member_subquery = member_subquery.where(ProjectMember.role == "owner")
    stmt = select(func.count(Project.id)).where(Project.id.in_(member_subquery))
    if membership_filter == "shared":
        stmt = stmt.where(
            Project.id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == user.id,
                    ProjectMember.role.in_(["editor", "viewer"]),
                )
            )
        )
    if status:
        stmt = stmt.where(Project.status == status)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (Project.name.ilike(like)) | (Project.description.ilike(like))
        )
    return (await db.execute(stmt)).scalar_one()


async def get_project_detail(
    db: AsyncSession, user: User, project_id: str
) -> ProjectDetail:
    project = await get_project_for_user(db, user, project_id)

    docs = (
        await db.execute(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project_id)
            .order_by(ProjectDocument.created_at.desc())
        )
    ).scalars().all()

    links = (
        await db.execute(
            select(ProjectPresentationLink)
            .where(ProjectPresentationLink.project_id == project_id)
            .order_by(ProjectPresentationLink.created_at.desc())
        )
    ).scalars().all()

    pres_ids = [str(l.presentation_id) for l in links]
    pres_map: dict[str, Presentation] = {}
    if pres_ids:
        pres_rows = (
            await db.execute(
                select(Presentation).where(Presentation.id.in_(pres_ids))
            )
        ).scalars().all()
        pres_map = {str(p.id): p for p in pres_rows}

    base = _to_list_item(project, len(docs), len(links))
    return ProjectDetail(
        **base.model_dump(),
        documents=[_doc_to_item(d) for d in docs],
        presentations=[
            _link_to_item(l, pres_map.get(str(l.presentation_id))) for l in links
        ],
    )


async def update_project(
    db: AsyncSession, user: User, project_id: str, req: ProjectUpdateRequest
) -> ProjectListItem:
    from app.services import activity_service

    project = await get_project_for_user(db, user, project_id, min_role="editor")

    changed: list[str] = []
    if req.name is not None and req.name != project.name:
        project.name = req.name
        changed.append("name")
    if req.description is not None and req.description != project.description:
        project.description = req.description
        changed.append("description")
    if req.tags is not None and (req.tags or []) != (project.tags or []):
        project.tags = req.tags
        changed.append("tags")
    if req.status is not None and req.status != project.status:
        project.status = req.status
        changed.append("status")

    await db.commit()
    await db.refresh(project)

    if changed:
        await activity_service.record(
            db,
            project_id=str(project.id),
            actor_id=str(user.id),
            action="project_updated",
            entity_type="project",
            entity_id=str(project.id),
            summary=f"Updated {', '.join(changed)}",
            metadata={"fields": changed, "status": project.status},
        )

    doc_count = (
        await db.execute(
            select(func.count(ProjectDocument.id)).where(
                ProjectDocument.project_id == project_id
            )
        )
    ).scalar_one()
    pres_count = (
        await db.execute(
            select(func.count(ProjectPresentationLink.id)).where(
                ProjectPresentationLink.project_id == project_id
            )
        )
    ).scalar_one()

    return _to_list_item(project, doc_count, pres_count)


async def delete_project(db: AsyncSession, user: User, project_id: str) -> None:
    from app.models.project_activity import ProjectActivity
    from app.models.project_member import ProjectMember

    project = await get_project_for_user(db, user, project_id, min_role="owner")

    # Delete child rows explicitly (no CASCADE configured at FK level)
    await db.execute(
        ProjectPresentationLink.__table__.delete().where(
            ProjectPresentationLink.project_id == project_id
        )
    )
    await db.execute(
        ProjectDocument.__table__.delete().where(
            ProjectDocument.project_id == project_id
        )
    )
    await db.execute(
        ProjectActivity.__table__.delete().where(
            ProjectActivity.project_id == project_id
        )
    )
    await db.execute(
        ProjectMember.__table__.delete().where(
            ProjectMember.project_id == project_id
        )
    )
    await db.delete(project)
    await db.commit()
