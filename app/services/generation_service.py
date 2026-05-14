from __future__ import annotations

import uuid
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.exceptions import NotFoundError, ValidationError
from app.core.storage import save_upload
from app.models.generation_job import GenerationJob
from app.models.template import Template
from app.models.user import User
from app.schemas.generation import GenerationJobResponse, GenerationStatusResponse
from app.utils.validators import validate_file_extension
from app.utils.file_handlers import safe_filename


async def start_generation(
    db: AsyncSession,
    user: User,
    template_id: str,
    file: UploadFile,
    logo_url: Optional[str] = None,
) -> GenerationJobResponse:
    template = (
        await db.execute(select(Template).where(Template.id == template_id))
    ).scalar_one_or_none()
    if not template or not template.is_active:
        raise NotFoundError(f"Template {template_id} not found")

    filename = safe_filename(file.filename or "upload")
    ext = validate_file_extension(filename)
    if not ext:
        raise ValidationError("File type not allowed. Allowed: .txt, .docx, .pdf")

    job_id = str(uuid.uuid4())
    saved_path = await save_upload(job_id, filename, file)

    job = GenerationJob(
        user_id=user.id,
        template_id=template.id,
        file_path=str(saved_path),
        job_id=job_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.tasks.generation_tasks import generate_presentation_task
    generate_presentation_task.delay(job_id, logo_url)

    return GenerationJobResponse(
        job_id=job_id,
        status=job.status,
        progress=job.progress,
    )


async def get_job_status(db: AsyncSession, user: User, job_id: str) -> GenerationStatusResponse:
    job = (
        await db.execute(select(GenerationJob).where(GenerationJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    if str(job.user_id) != str(user.id) and user.role != "admin":
        raise NotFoundError(f"Job {job_id} not found")

    return GenerationStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        presentation_id=job.result_presentation_id or None,
        error_message=job.error_message,
    )


async def list_user_jobs(db: AsyncSession, user: User) -> list[GenerationStatusResponse]:
    jobs = (
        await db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id))
    ).scalars().all()
    return [
        GenerationStatusResponse(
            job_id=j.job_id,
            status=j.status,
            progress=j.progress,
            result_presentation_id=j.result_presentation_id or None,
            error_message=j.error_message,
        )
        for j in jobs
    ]
