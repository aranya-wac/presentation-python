from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.presentation import Presentation
from app.models.user import User

router = APIRouter(prefix="/usage", tags=["usage"])


# Free-tier monthly cap. When we add paid plans this becomes a per-plan lookup
# (e.g. settings.PLAN_LIMITS[user.plan]). For now everyone is on Free.
FREE_MONTHLY_GENERATIONS = 50


class UsageResponse(BaseModel):
    plan: str  # "free" | "pro" | "team"
    plan_label: str  # display string
    generations_used: int
    generations_limit: int | None  # None = unlimited
    period_start: str  # ISO date
    period_end: str  # ISO date
    upgrade_available: bool


def _start_of_month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_next_month(now: datetime) -> datetime:
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("", response_model=UsageResponse)
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    """Return the current user's plan + generation usage for this month.

    "Generation" = a saved presentation. We count rows from the
    `presentations` table so this stays accurate even after we add server-side
    token persistence later.
    """
    now = datetime.now(timezone.utc)
    period_start = _start_of_month(now)
    period_end = _start_of_next_month(now)

    # Naive datetime comparison: SQLAlchemy will adapt depending on the column
    # type. The presentations table stores `created_at` with timezone, so we
    # compare against aware datetimes.
    stmt = (
        select(func.count())
        .select_from(Presentation)
        .where(
            Presentation.user_id == str(current_user.id),
            Presentation.created_at >= period_start,
        )
    )
    used = int((await db.execute(stmt)).scalar() or 0)

    return UsageResponse(
        plan="free",
        plan_label="Free",
        generations_used=used,
        generations_limit=FREE_MONTHLY_GENERATIONS,
        period_start=period_start.date().isoformat(),
        period_end=period_end.date().isoformat(),
        upgrade_available=True,
    )
