from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.generation import GenerationJobResponse, GenerationStatusResponse
from app.services import generation_service

router = APIRouter(prefix="/generate", tags=["generation"])


@router.post("", response_model=GenerationJobResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("20/hour")
async def start_generation(
    request: Request,
    template_id: str = Form(...),
    file: UploadFile = File(...),
    logo_url: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerationJobResponse:
    return await generation_service.start_generation(db, current_user, template_id, file, logo_url)


@router.get("/status/{job_id}", response_model=GenerationStatusResponse)
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerationStatusResponse:
    return await generation_service.get_job_status(db, current_user, job_id)
