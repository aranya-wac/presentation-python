from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini_client import generate_json
from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.presentation import Presentation
from app.models.user import User
from app.schemas.presentation import (
    CreatePresentationRequest,
    PresentationDetail,
    PresentationListItem,
    SlideSchema,
    UpdatePresentationRequest,
)
from app.services import presentation_service, version_service
from sqlalchemy import select

router = APIRouter(prefix="/presentations", tags=["presentations"])


@router.get("", response_model=list[PresentationListItem])
async def list_presentations(
    is_preview: Optional[bool] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PresentationListItem]:
    return await presentation_service.list_presentations(db, current_user, is_preview)


@router.post("", response_model=PresentationDetail, status_code=status.HTTP_201_CREATED)
async def create_presentation(
    req: CreatePresentationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    return await presentation_service.create_presentation(db, current_user, req)


@router.get("/{presentation_id}", response_model=PresentationDetail)
async def get_presentation(
    presentation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    return await presentation_service.get_presentation(db, current_user, presentation_id)


@router.patch("/{presentation_id}", response_model=PresentationDetail)
async def update_presentation(
    presentation_id: str,
    req: UpdatePresentationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    return await presentation_service.update_presentation(db, current_user, presentation_id, req)


@router.delete("/{presentation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_presentation(
    presentation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await presentation_service.delete_presentation(db, current_user, presentation_id)


class SlideChatRequest(BaseModel):
    slide: dict[str, Any]
    message: str


class SlideChatResponse(BaseModel):
    updated_slide: dict[str, Any]
    response: str


@router.post("/{presentation_id}/slide-chat", response_model=SlideChatResponse)
async def slide_chat(
    presentation_id: str,
    req: SlideChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SlideChatResponse:
    slide_json = json.dumps(req.slide)
    prompt = f"""You are an AI slide editor. The user wants to modify a presentation slide.

Current slide (JSON):
{slide_json}

User request: "{req.message}"

Apply the requested change to the slide and return a JSON object with exactly these two keys:
- "updated_slide": the full modified slide JSON (same structure as the input, with changes applied)
- "response": a short confirmation message describing what was changed (1-2 sentences)

Rules:
- Only change what the user asked for. Do not restructure or remove blocks unless asked.
- For background changes: set slide.background.type to "color" or "gradient" and slide.background.value to the CSS value.
- For text color changes: update styling.color on the relevant blocks.
- For font size changes: update styling.font_size on the relevant blocks.
- For content changes: update the block's content field.
- Return valid JSON only, no markdown fences."""

    try:
        result = await generate_json(prompt)
        return SlideChatResponse(
            updated_slide=result["updated_slide"],
            response=result["response"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Version history ──────────────────────────────────────────────────────────


async def _load_owned_presentation(
    db: AsyncSession, user: User, presentation_id: str
) -> Presentation:
    p = (
        await db.execute(select(Presentation).where(Presentation.id == presentation_id))
    ).scalar_one_or_none()
    if not p:
        raise NotFoundError(f"Presentation {presentation_id} not found")
    if p.user_id and str(p.user_id) != str(user.id) and user.role != "admin":
        raise ForbiddenError()
    return p


class VersionItem(BaseModel):
    id: str
    presentation_id: str
    version_number: int
    title: str
    slide_count: int
    theme_id: str
    created_by: Optional[str]
    label: str
    created_at: str


class CreateVersionPayload(BaseModel):
    label: str = ""


@router.get("/{presentation_id}/versions", response_model=list[VersionItem])
async def list_versions(
    presentation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VersionItem]:
    await _load_owned_presentation(db, current_user, presentation_id)
    versions = await version_service.list_versions(db, presentation_id)
    return [VersionItem(**version_service.to_dict(v)) for v in versions]


@router.post("/{presentation_id}/versions", response_model=VersionItem, status_code=201)
async def create_version(
    presentation_id: str,
    payload: CreateVersionPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VersionItem:
    p = await _load_owned_presentation(db, current_user, presentation_id)
    v = await version_service.force_snapshot(db, p, current_user, label=payload.label)
    return VersionItem(**version_service.to_dict(v))


@router.post("/{presentation_id}/versions/{version_id}/restore", response_model=PresentationDetail)
async def restore_version(
    presentation_id: str,
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PresentationDetail:
    p = await _load_owned_presentation(db, current_user, presentation_id)
    await version_service.restore_version(db, p, current_user, version_id)
    return await presentation_service.get_presentation(db, current_user, presentation_id)
