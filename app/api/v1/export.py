from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.schemas.export import ExportJobResponse, ExportRequest
from app.services import export_service

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/{presentation_id}/{format}", response_model=ExportJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_export(
    presentation_id: str,
    format: Literal["pdf", "pptx", "html"],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportJobResponse:
    req = ExportRequest(format=format)
    return await export_service.start_export(db, current_user, presentation_id, req)


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExportJobResponse:
    return await export_service.get_export_status(db, current_user, job_id)


@router.get("/download/{job_id}")
async def download_export(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job_resp = await export_service.get_export_status(db, current_user, job_id)
    if job_resp.status != "completed" or not job_resp.file_url:
        raise NotFoundError("Export file not ready")
    if not os.path.exists(job_resp.file_url):
        raise NotFoundError("Export file not found on disk")
    return FileResponse(
        path=job_resp.file_url,
        filename=os.path.basename(job_resp.file_url),
        media_type="application/octet-stream",
    )
