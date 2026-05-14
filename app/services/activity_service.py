"""Project activity feed — append-only event log with read API.

Writes are best-effort: a logging failure must never break the underlying
project/document/generation flow. All write helpers swallow exceptions.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_activity import ProjectActivity
from app.models.user import User
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def record(
    db: AsyncSession,
    *,
    project_id: str,
    actor_id: Optional[str],
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    summary: str = "",
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    """Append an activity row. Safe — never raises.

    Use commit=False if the caller is mid-transaction and will commit later.
    """
    try:
        row = ProjectActivity(
            project_id=str(project_id),
            actor_id=str(actor_id) if actor_id else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            summary=summary,
            metadata_json=metadata or {},
        )
        db.add(row)
        if commit:
            await db.commit()
    except Exception as exc:
        logger.warning(f"activity.record failed for {action}: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass


async def list_activities(
    db: AsyncSession,
    project_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (items, total) for a project's activity feed, newest first.

    Items are pre-formatted dicts — the response shape is intentionally close
    to what the frontend renders, including actor display name when available.
    """
    total = (
        await db.execute(
            select(func.count(ProjectActivity.id)).where(
                ProjectActivity.project_id == project_id
            )
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(ProjectActivity)
            .where(ProjectActivity.project_id == project_id)
            .order_by(desc(ProjectActivity.created_at))
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    actor_ids = list({r.actor_id for r in rows if r.actor_id})
    actor_map: dict[str, str] = {}
    if actor_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(actor_ids)))
        ).scalars().all()
        actor_map = {str(u.id): (u.full_name or u.email) for u in users}

    items = [
        {
            "id": str(r.id),
            "project_id": str(r.project_id),
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "actor_name": actor_map.get(str(r.actor_id), "") if r.actor_id else "",
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "summary": r.summary,
            "metadata": r.metadata_json or {},
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
    return items, total
