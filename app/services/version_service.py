from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.presentation import Presentation
from app.models.presentation_version import PresentationVersion
from app.models.user import User

# Don't snapshot more often than this. The intent is "user can roll back the
# last hour of edits", not "every keystroke is a savepoint".
MIN_SNAPSHOT_INTERVAL = timedelta(minutes=2)
# Cap retained versions per deck to keep storage bounded.
MAX_VERSIONS_PER_DECK = 50


async def list_versions(db: AsyncSession, presentation_id: str) -> list[PresentationVersion]:
    return list(
        (
            await db.execute(
                select(PresentationVersion)
                .where(PresentationVersion.presentation_id == presentation_id)
                .order_by(desc(PresentationVersion.version_number))
            )
        ).scalars().all()
    )


async def _next_version_number(db: AsyncSession, presentation_id: str) -> int:
    last = (
        await db.execute(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == presentation_id)
            .order_by(desc(PresentationVersion.version_number))
            .limit(1)
        )
    ).scalar_one_or_none()
    return (last.version_number + 1) if last else 1


async def maybe_snapshot(
    db: AsyncSession, p: Presentation, user: User, label: str = ""
) -> Optional[PresentationVersion]:
    """Snapshot the deck if enough time has elapsed since the last one.

    Returns the new version, or None if we skipped (debounced).
    Caller must commit.
    """
    last = (
        await db.execute(
            select(PresentationVersion)
            .where(PresentationVersion.presentation_id == p.id)
            .order_by(desc(PresentationVersion.version_number))
            .limit(1)
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if last and last.created_at and (now - last.created_at.replace(tzinfo=timezone.utc)) < MIN_SNAPSHOT_INTERVAL:
        return None

    version = PresentationVersion(
        presentation_id=p.id,
        version_number=(last.version_number + 1) if last else 1,
        title=p.title,
        slides=copy.deepcopy(p.slides or []),
        theme_id=p.theme_id,
        created_by=str(user.id) if user else None,
        label=label or "",
    )
    db.add(version)

    # Retention: drop the oldest versions beyond the cap.
    if last and version.version_number > MAX_VERSIONS_PER_DECK:
        oldest = (
            await db.execute(
                select(PresentationVersion)
                .where(PresentationVersion.presentation_id == p.id)
                .order_by(PresentationVersion.version_number)
                .limit(version.version_number - MAX_VERSIONS_PER_DECK)
            )
        ).scalars().all()
        for v in oldest:
            await db.delete(v)

    return version


async def force_snapshot(
    db: AsyncSession, p: Presentation, user: User, label: str = ""
) -> PresentationVersion:
    """Always create a snapshot (e.g. before an export, or on user request)."""
    next_num = await _next_version_number(db, p.id)
    version = PresentationVersion(
        presentation_id=p.id,
        version_number=next_num,
        title=p.title,
        slides=copy.deepcopy(p.slides or []),
        theme_id=p.theme_id,
        created_by=str(user.id) if user else None,
        label=label or "",
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return version


async def restore_version(
    db: AsyncSession, p: Presentation, user: User, version_id: str
) -> Presentation:
    v = (
        await db.execute(
            select(PresentationVersion).where(PresentationVersion.id == version_id)
        )
    ).scalar_one_or_none()
    if not v or v.presentation_id != p.id:
        raise NotFoundError("Version not found")

    # Snapshot current state first so the restore itself is undoable.
    await force_snapshot(db, p, user, label=f"Auto-snapshot before restoring v{v.version_number}")

    p.slides = copy.deepcopy(v.slides or [])
    p.theme_id = v.theme_id
    if v.title:
        p.title = v.title
    await db.commit()
    await db.refresh(p)
    return p


def to_dict(v: PresentationVersion) -> dict:
    return {
        "id": str(v.id),
        "presentation_id": str(v.presentation_id),
        "version_number": v.version_number,
        "title": v.title,
        "slide_count": len(v.slides or []),
        "theme_id": str(v.theme_id),
        "created_by": str(v.created_by) if v.created_by else None,
        "label": v.label,
        "created_at": v.created_at.isoformat() if v.created_at else "",
    }
