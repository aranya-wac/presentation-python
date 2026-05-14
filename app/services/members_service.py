"""Project membership + RBAC.

Role hierarchy: viewer < editor < owner.

`require_membership` is the single entry point used by every project-scoped
endpoint to enforce access. Callers pass the minimum role required and
either get back a `ProjectMember` row or hit a Forbidden/NotFound error.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
VALID_ROLES = set(ROLE_RANK.keys())


def _has_at_least(actual: str, required: str) -> bool:
    return ROLE_RANK.get(actual, 0) >= ROLE_RANK.get(required, 99)


async def get_membership(
    db: AsyncSession, project_id: str, user_id: str
) -> Optional[ProjectMember]:
    return (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def require_membership(
    db: AsyncSession,
    user: User,
    project_id: str,
    *,
    min_role: str = "viewer",
) -> ProjectMember:
    """Enforce that `user` has at least `min_role` on the project.

    Returns the `ProjectMember` row. Raises NotFoundError if the project
    doesn't exist (so existence isn't leaked to non-members) or
    ForbiddenError if the user is a member but at insufficient privilege.
    Platform admins (`user.role == 'admin'`) bypass the check and get
    a synthetic owner membership.
    """
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if not project:
        raise NotFoundError(f"Project {project_id} not found")

    if user.role == "admin":
        return ProjectMember(
            id="admin-bypass",
            project_id=str(project.id),
            user_id=str(user.id),
            role="owner",
            invited_by=None,
        )

    membership = await get_membership(db, str(project.id), str(user.id))
    if not membership:
        # Hide existence from non-members.
        raise NotFoundError(f"Project {project_id} not found")
    if not _has_at_least(membership.role, min_role):
        raise ForbiddenError(
            f"Requires '{min_role}' role on this project (you are '{membership.role}')."
        )
    return membership


async def list_accessible_project_ids(
    db: AsyncSession, user: User
) -> list[str]:
    rows = (
        await db.execute(
            select(ProjectMember.project_id).where(
                ProjectMember.user_id == user.id
            )
        )
    ).scalars().all()
    return [str(r) for r in rows]


async def list_members(
    db: AsyncSession, project_id: str
) -> list[dict]:
    rows = (
        await db.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
    ).scalars().all()
    user_ids = list({r.user_id for r in rows})
    users = (
        await db.execute(select(User).where(User.id.in_(user_ids)))
    ).scalars().all() if user_ids else []
    umap = {str(u.id): u for u in users}

    items = []
    for m in rows:
        u = umap.get(str(m.user_id))
        items.append(
            {
                "id": str(m.id),
                "project_id": str(m.project_id),
                "user_id": str(m.user_id),
                "email": u.email if u else "",
                "full_name": (u.full_name if u else "") or "",
                "role": m.role,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
        )
    items.sort(key=lambda x: (-ROLE_RANK.get(x["role"], 0), x["full_name"]))
    return items


async def add_member(
    db: AsyncSession,
    actor: User,
    project_id: str,
    *,
    email: str,
    role: str,
) -> dict:
    if role not in VALID_ROLES:
        raise ValidationError(f"Invalid role '{role}'")

    target = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if not target:
        raise NotFoundError(f"No user with email '{email}'")

    existing = await get_membership(db, project_id, str(target.id))
    if existing:
        raise ValidationError(
            f"{target.email} is already a {existing.role} on this project"
        )

    member = ProjectMember(
        project_id=project_id,
        user_id=str(target.id),
        role=role,
        invited_by=str(actor.id),
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=project_id,
        actor_id=str(actor.id),
        action="member_added",
        entity_type="member",
        entity_id=str(member.id),
        summary=f"Added {target.email} as {role}",
        metadata={"target_user_id": str(target.id), "role": role},
    )

    return {
        "id": str(member.id),
        "project_id": str(member.project_id),
        "user_id": str(member.user_id),
        "email": target.email,
        "full_name": target.full_name or "",
        "role": member.role,
        "created_at": member.created_at.isoformat() if member.created_at else "",
    }


async def update_member_role(
    db: AsyncSession,
    actor: User,
    project_id: str,
    member_id: str,
    new_role: str,
) -> dict:
    if new_role not in VALID_ROLES:
        raise ValidationError(f"Invalid role '{new_role}'")

    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.id == member_id,
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not member:
        raise NotFoundError(f"Member {member_id} not found")

    # Prevent demoting the last owner — the project must always have one.
    if member.role == "owner" and new_role != "owner":
        owner_count = (
            await db.execute(
                select(func.count(ProjectMember.id)).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.role == "owner",
                )
            )
        ).scalar_one()
        if owner_count <= 1:
            raise ValidationError(
                "Cannot demote the last owner — promote someone else first."
            )

    old_role = member.role
    member.role = new_role
    await db.commit()
    await db.refresh(member)

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=project_id,
        actor_id=str(actor.id),
        action="member_role_changed",
        entity_type="member",
        entity_id=str(member.id),
        summary=f"Changed role from {old_role} to {new_role}",
        metadata={"target_user_id": str(member.user_id), "old": old_role, "new": new_role},
    )

    target = (
        await db.execute(select(User).where(User.id == member.user_id))
    ).scalar_one_or_none()
    return {
        "id": str(member.id),
        "project_id": str(member.project_id),
        "user_id": str(member.user_id),
        "email": target.email if target else "",
        "full_name": (target.full_name if target else "") or "",
        "role": member.role,
        "created_at": member.created_at.isoformat() if member.created_at else "",
    }


async def remove_member(
    db: AsyncSession,
    actor: User,
    project_id: str,
    member_id: str,
) -> None:
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.id == member_id,
                ProjectMember.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not member:
        raise NotFoundError(f"Member {member_id} not found")

    if member.role == "owner":
        owner_count = (
            await db.execute(
                select(func.count(ProjectMember.id)).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.role == "owner",
                )
            )
        ).scalar_one()
        if owner_count <= 1:
            raise ValidationError(
                "Cannot remove the last owner — promote someone else first."
            )

    target_user_id = str(member.user_id)
    await db.delete(member)
    await db.commit()

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=project_id,
        actor_id=str(actor.id),
        action="member_removed",
        entity_type="member",
        entity_id=member_id,
        summary="Removed member",
        metadata={"target_user_id": target_user_id},
    )
