from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.exceptions import GeminiError
from app.core.rate_limit import limiter
from app.core.storage import BASE_DIR
from app.models.user import User
from app.utils.logger import get_logger

router = APIRouter(prefix="/images", tags=["images"])
logger = get_logger(__name__)

# Generated images live under storage/generated and are mounted by main.py at
# /generated. Keeping them on disk lets them survive a server restart and
# reload from the same URL the editor saved as the block content.
GENERATED_DIR = BASE_DIR / "storage" / "generated"


class GenerateImageRequest(BaseModel):
    prompt: str
    style: str = ""


class GenerateImageResponse(BaseModel):
    url: str
    mime_type: str


@router.post("/generate", response_model=GenerateImageResponse)
@limiter.limit("20/hour")
async def generate_image(
    request: Request,
    payload: GenerateImageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerateImageResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required.")
    if len(prompt) > 2000:
        raise HTTPException(status_code=400, detail="Prompt is too long.")

    final_prompt = prompt
    if payload.style:
        final_prompt = f"{prompt}\n\nStyle: {payload.style}"

    try:
        image_bytes, mime = await gemini_client.generate_image(final_prompt)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Image generation failed")
        raise HTTPException(status_code=500, detail=f"Image generation failed: {exc}")

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ext = mime.split("/")[-1] if "/" in mime else "png"
    if ext == "jpeg":
        ext = "jpg"
    filename = f"{uuid.uuid4()}.{ext}"
    dest = GENERATED_DIR / filename
    dest.write_bytes(image_bytes)

    return GenerateImageResponse(url=f"/generated/{filename}", mime_type=mime)
