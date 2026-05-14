from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.models.export_job import ExportJob
from app.models.presentation import Presentation
from app.models.user import User
from app.schemas.export import ExportJobResponse, ExportRequest


def _to_response(j: ExportJob) -> ExportJobResponse:
    return ExportJobResponse(
        job_id=str(j.id),
        presentation_id=str(j.presentation_id),
        format=j.format,
        status=j.status,
        progress=j.progress,
        file_url=j.file_path,
        file_size=j.file_size,
        error_message=j.error_message,
    )


async def start_export(
    db: AsyncSession, user: User, presentation_id: str, req: ExportRequest
) -> ExportJobResponse:
    p = (
        await db.execute(select(Presentation).where(Presentation.id == presentation_id))
    ).scalar_one_or_none()
    if not p:
        raise NotFoundError(f"Presentation {presentation_id} not found")

    job = ExportJob(
        user_id=user.id,
        presentation_id=p.id,
        format=req.format,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Run export in the background via asyncio (no Celery worker required)
    import asyncio
    from app.tasks.export_tasks import _run_export
    asyncio.create_task(_run_export(str(job.id)))

    return _to_response(job)


async def get_export_status(db: AsyncSession, user: User, job_id: str) -> ExportJobResponse:
    job = (
        await db.execute(select(ExportJob).where(ExportJob.id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Export job {job_id} not found")
    if str(job.user_id) != str(user.id) and user.role != "admin":
        raise NotFoundError(f"Export job {job_id} not found")
    return _to_response(job)
