from __future__ import annotations

import json
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models.user import User
from app.schemas.project import (
    DocumentDetail,
    DocumentListItem,
    DocumentUpdateRequest,
    GenerateFromProjectRequest,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectListItem,
    ProjectListPage,
    ProjectPresentationItem,
    ProjectUpdateRequest,
    RegenerateRequest,
)
from app.services import (
    activity_service,
    document_service,
    members_service,
    project_service,
    role_generation_service,
)

router = APIRouter(prefix="/projects", tags=["projects"])


# ── Roles helper (no project context) ────────────────────────────────────────

@router.get("/roles", summary="List supported generation roles")
async def list_roles(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await role_generation_service.list_supported_roles(db)


# ── Project CRUD ─────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ProjectListItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    req: ProjectCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectListItem:
    return await project_service.create_project(db, user, req)


@router.get("")
async def list_projects(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    sort: str = Query("updated_desc"),
    paginated: bool = Query(False, description="Return paged response"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    membership: Optional[str] = Query(
        None, description="Filter: 'owned' | 'shared' (omit for all accessible)"
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Default response is `list[ProjectListItem]` for backward compatibility.
    Pass `?paginated=true` to receive a `ProjectListPage` envelope with
    `total`, `limit`, and `offset`. The unversioned shape may be deprecated
    in a future release; new clients should opt into pagination.

    `membership` filters by membership role:
      - `owned`  — projects where the caller is an owner
      - `shared` — projects where the caller is editor/viewer (someone else owns)
      - omitted  — all projects the caller can see
    """
    if membership not in (None, "owned", "shared"):
        membership = None
    if paginated:
        items = await project_service.list_projects(
            db, user, status=status_filter, search=search, sort=sort,
            limit=limit, offset=offset, membership_filter=membership,
        )
        total = await project_service.count_projects(
            db, user, status=status_filter, search=search,
            membership_filter=membership,
        )
        return ProjectListPage(items=items, total=total, limit=limit, offset=offset)

    return await project_service.list_projects(
        db, user, status=status_filter, search=search, sort=sort,
        membership_filter=membership,
    )


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectDetail:
    return await project_service.get_project_detail(db, user, project_id)


@router.patch("/{project_id}", response_model=ProjectListItem)
async def update_project(
    project_id: str,
    req: ProjectUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectListItem:
    return await project_service.update_project(db, user, project_id, req)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await project_service.delete_project(db, user, project_id)


# ── Documents ────────────────────────────────────────────────────────────────

@router.get("/{project_id}/documents", response_model=list[DocumentListItem])
async def list_documents(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentListItem]:
    return await document_service.list_documents(db, user, project_id)


@router.post(
    "/{project_id}/documents",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    tags: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    parsed_tags: Optional[list[str]] = None
    if tags:
        try:
            decoded = json.loads(tags)
            if isinstance(decoded, list):
                parsed_tags = [str(t) for t in decoded]
            else:
                raise ValidationError("'tags' must be a JSON array of strings")
        except json.JSONDecodeError:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]
    return await document_service.upload_document(
        db, user, project_id, file, tags=parsed_tags
    )


@router.get(
    "/{project_id}/documents/{document_id}",
    response_model=DocumentDetail,
)
async def get_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    return await document_service.get_document(db, user, project_id, document_id)


@router.patch(
    "/{project_id}/documents/{document_id}",
    response_model=DocumentDetail,
)
async def update_document(
    project_id: str,
    document_id: str,
    req: DocumentUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    return await document_service.update_document(
        db, user, project_id, document_id, req
    )


@router.delete(
    "/{project_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await document_service.delete_document(db, user, project_id, document_id)


@router.post(
    "/{project_id}/documents/{document_id}/retry-extraction",
    response_model=DocumentDetail,
)
async def retry_extraction(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentDetail:
    return await document_service.retry_extraction(db, user, project_id, document_id)


@router.get(
    "/{project_id}/documents/{document_id}/status",
    summary="Lightweight extraction-status poll",
)
async def get_document_status(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await document_service.get_extraction_status(
        db, user, project_id, document_id
    )


@router.get(
    "/{project_id}/documents/{document_id}/download",
    summary="Download the original uploaded file",
)
async def download_document(
    project_id: str,
    document_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    doc = await document_service.get_document_for_download(
        db, user, project_id, document_id
    )
    path = Path(doc.storage_path)
    if not path.exists():
        raise NotFoundError("Document file is missing on disk")
    media = {
        "pdf": "application/pdf",
        "docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "txt": "text/plain",
    }.get(doc.format, "application/octet-stream")
    return FileResponse(
        path=str(path),
        media_type=media,
        filename=doc.original_filename,
    )


# ── Activity feed ────────────────────────────────────────────────────────────

@router.get(
    "/{project_id}/activities",
    summary="List project activity (audit log)",
)
async def list_project_activities(
    project_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Reuse the ownership check
    await project_service.get_project_for_user(db, user, project_id)
    items, total = await activity_service.list_activities(
        db, project_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ── Members (collaborators) ──────────────────────────────────────────────────

class _AddMemberRequest(BaseModel):  # type: ignore[name-defined]
    email: str
    role: str  # owner|editor|viewer


class _UpdateMemberRequest(BaseModel):  # type: ignore[name-defined]
    role: str


@router.get("/{project_id}/members", summary="List project members")
async def list_members(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    # Any member can see the roster
    await members_service.require_membership(db, user, project_id, min_role="viewer")
    return await members_service.list_members(db, project_id)


@router.post(
    "/{project_id}/members",
    status_code=status.HTTP_201_CREATED,
    summary="Add a member by email",
)
async def add_member(
    project_id: str,
    req: _AddMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await members_service.require_membership(db, user, project_id, min_role="owner")
    return await members_service.add_member(
        db, user, project_id, email=req.email, role=req.role
    )


@router.patch(
    "/{project_id}/members/{member_id}",
    summary="Update a member's role",
)
async def update_member(
    project_id: str,
    member_id: str,
    req: _UpdateMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await members_service.require_membership(db, user, project_id, min_role="owner")
    return await members_service.update_member_role(
        db, user, project_id, member_id, req.role
    )


@router.delete(
    "/{project_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
)
async def remove_member(
    project_id: str,
    member_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await members_service.require_membership(db, user, project_id, min_role="owner")
    await members_service.remove_member(db, user, project_id, member_id)


# ── Role-based generation ────────────────────────────────────────────────────

@router.post(
    "/{project_id}/generate",
    response_model=ProjectPresentationItem,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a role-specific presentation from project documents",
)
async def generate_presentation(
    project_id: str,
    req: GenerateFromProjectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPresentationItem:
    return await role_generation_service.generate_role_presentation(
        db, user, project_id, req
    )


@router.delete(
    "/{project_id}/presentations/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a generated deck (removes link + underlying presentation)",
)
async def delete_project_presentation(
    project_id: str,
    link_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await role_generation_service.delete_project_presentation(
        db, user, project_id, link_id
    )


@router.post(
    "/{project_id}/presentations/{link_id}/regenerate",
    response_model=ProjectPresentationItem,
    status_code=status.HTTP_201_CREATED,
    summary="Regenerate a deck — preserves history via prior_link_id chain",
)
async def regenerate_project_presentation(
    project_id: str,
    link_id: str,
    req: RegenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPresentationItem:
    return await role_generation_service.regenerate_project_presentation(
        db, user, project_id, link_id, req.model_dump(exclude_none=True)
    )
