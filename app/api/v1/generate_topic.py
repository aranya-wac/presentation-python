from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.user import User
from app.services import topic_generation_service

router = APIRouter(prefix="/generate", tags=["generation"])

_VALID_DEPTHS = {"shallow", "standard", "deep"}


@router.post("/topic")
@limiter.limit("10/hour")
async def generate_from_topic(
    request: Request,
    topic: str = Form(...),
    audience: str = Form(""),
    style: str = Form(""),
    slide_count: int = Form(10),
    depth: str = Form("standard"),
    level: str = Form("simple"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE: research a current topic from live news and stream a presentation.

    Events: status, search_results, extracted, research, outline, theme,
    slide (one per slide), complete, warning, error.
    """
    topic = (topic or "").strip()
    if len(topic) < 2:
        raise HTTPException(status_code=400, detail="Topic is required.")
    if len(topic) > 200:
        raise HTTPException(status_code=400, detail="Topic is too long.")
    if depth not in _VALID_DEPTHS:
        raise HTTPException(status_code=400, detail=f"depth must be one of {sorted(_VALID_DEPTHS)}.")

    return StreamingResponse(
        topic_generation_service.stream_generation(
            topic=topic,
            audience=audience,
            style=style,
            slide_count=slide_count,
            depth=depth,
            level=level,
            db=db,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
