from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services import share_service

router = APIRouter(prefix="/share", tags=["share"])


@router.get("/view/{presentation_id}", response_class=HTMLResponse)
async def view_shared_presentation(
    presentation_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    html = await share_service.render_shared_html(db, presentation_id)
    return HTMLResponse(content=html, status_code=200)
