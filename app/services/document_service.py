from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.storage import BASE_DIR
from app.extractors.extractor_factory import extract_content
from app.models.project import ProjectDocument
from app.models.user import User
from app.schemas.project import (
    DocumentDetail,
    DocumentListItem,
    DocumentUpdateRequest,
)
from app.services.project_service import get_project_for_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_DOCS_DIR = BASE_DIR / "storage" / "project_documents"
ALLOWED_FORMATS = {"pdf", "docx", "txt"}


def _ts(dt) -> str:
    return dt.isoformat() if dt else ""


def _to_item(d: ProjectDocument) -> DocumentListItem:
    return DocumentListItem(
        id=str(d.id),
        project_id=str(d.project_id),
        filename=d.filename,
        original_filename=d.original_filename,
        format=d.format,
        size_bytes=d.size_bytes,
        version=d.version,
        extraction_status=d.extraction_status,
        tags=d.tags or [],
        uploaded_by=str(d.uploaded_by),
        created_at=_ts(d.created_at),
        updated_at=_ts(d.updated_at),
    )


def _to_detail(d: ProjectDocument) -> DocumentDetail:
    return DocumentDetail(
        id=str(d.id),
        project_id=str(d.project_id),
        filename=d.filename,
        original_filename=d.original_filename,
        format=d.format,
        size_bytes=d.size_bytes,
        version=d.version,
        extraction_status=d.extraction_status,
        tags=d.tags or [],
        uploaded_by=str(d.uploaded_by),
        created_at=_ts(d.created_at),
        updated_at=_ts(d.updated_at),
        extracted_text=d.extracted_text,
        extraction_error=d.extraction_error,
        storage_path=d.storage_path,
    )


def _detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_FORMATS:
        raise ValidationError(
            f"Unsupported file format '.{ext}'. Allowed: {sorted(ALLOWED_FORMATS)}"
        )
    return ext


async def _save_to_disk(project_id: str, doc_id: str, original_name: str, file: UploadFile) -> tuple[Path, int]:
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    dest_dir = PROJECT_DOCS_DIR / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{doc_id}_{Path(original_name).name}"
    dest = dest_dir / safe_name

    total = 0
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                dest.unlink(missing_ok=True)
                raise ValidationError(
                    f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB"
                )
            f.write(chunk)
    return dest, total


async def _extract_async(path: Path) -> str:
    return await asyncio.to_thread(extract_content, path)


# ── Public API ───────────────────────────────────────────────────────────────

async def upload_document(
    db: AsyncSession,
    user: User,
    project_id: str,
    file: UploadFile,
    tags: Optional[list[str]] = None,
) -> DocumentDetail:
    project = await get_project_for_user(db, user, project_id, min_role="editor")

    original_filename = file.filename or "upload"
    fmt = _detect_format(original_filename)

    doc_id = str(uuid.uuid4())
    dest, size = await _save_to_disk(str(project.id), doc_id, original_filename, file)

    # Compute next version number for this logical filename within the project
    existing_max = (
        await db.execute(
            select(func.max(ProjectDocument.version)).where(
                ProjectDocument.project_id == project_id,
                ProjectDocument.original_filename == original_filename,
            )
        )
    ).scalar()
    next_version = (existing_max or 0) + 1

    doc = ProjectDocument(
        id=doc_id,
        project_id=str(project.id),
        uploaded_by=str(user.id),
        filename=dest.name,
        original_filename=original_filename,
        format=fmt,
        size_bytes=size,
        version=next_version,
        storage_path=str(dest),
        extraction_status="pending",
        tags=tags or [],
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=str(doc.project_id),
        actor_id=str(user.id),
        action="document_uploaded",
        entity_type="document",
        entity_id=str(doc.id),
        summary=f"Uploaded {original_filename}",
        metadata={"format": fmt, "size_bytes": size, "version": next_version},
    )

    # Try to dispatch extraction to Celery so large PDFs don't hold the request.
    # If the worker/broker is unreachable AND EXTRACTION_SYNC_FALLBACK is on,
    # fall back to inline extraction so dev workflow stays unblocked.
    dispatched = await _dispatch_extraction(doc.id)
    if not dispatched:
        try:
            text = await _extract_async(dest)
            doc.extracted_text = text
            doc.extraction_status = "complete"
            await db.commit()
            await db.refresh(doc)
            await activity_service.record(
                db,
                project_id=str(doc.project_id),
                actor_id=str(user.id),
                action="extraction_completed",
                entity_type="document",
                entity_id=str(doc.id),
                summary=f"Extracted text from {original_filename}",
            )
        except Exception as exc:
            logger.warning(f"Extraction failed for {dest}: {exc}")
            doc.extraction_status = "failed"
            doc.extraction_error = str(exc)
            await db.commit()
            await db.refresh(doc)
            await activity_service.record(
                db,
                project_id=str(doc.project_id),
                actor_id=str(user.id),
                action="extraction_failed",
                entity_type="document",
                entity_id=str(doc.id),
                summary=f"Extraction failed for {original_filename}",
                metadata={"error": str(exc)},
            )

    return _to_detail(doc)


async def _dispatch_extraction(document_id: str) -> bool:
    """Enqueue the Celery extraction task when a worker is configured.

    Returns True if dispatched (caller should NOT run inline extraction).
    Returns False if running in sync mode (caller MUST run inline extraction).

    We don't try to auto-detect a running worker — `apply_async` to a
    reachable broker silently queues the task even if no consumer exists,
    leaving docs stuck in "pending" forever. So `USE_EXTRACTION_WORKER`
    must be set explicitly when a worker is deployed.
    """
    if not settings.USE_EXTRACTION_WORKER:
        return False
    try:
        from app.tasks.extraction_tasks import extract_document_text_task
        extract_document_text_task.delay(document_id)
        return True
    except Exception as exc:
        logger.warning(f"Celery dispatch failed: {exc}; running inline as fallback")
        return False


async def list_documents(
    db: AsyncSession, user: User, project_id: str
) -> list[DocumentListItem]:
    await get_project_for_user(db, user, project_id)
    rows = (
        await db.execute(
            select(ProjectDocument)
            .where(ProjectDocument.project_id == project_id)
            .order_by(ProjectDocument.created_at.desc())
        )
    ).scalars().all()
    return [_to_item(d) for d in rows]


async def get_document(
    db: AsyncSession, user: User, project_id: str, document_id: str
) -> DocumentDetail:
    await get_project_for_user(db, user, project_id)
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    return _to_detail(doc)


async def update_document(
    db: AsyncSession,
    user: User,
    project_id: str,
    document_id: str,
    req: DocumentUpdateRequest,
) -> DocumentDetail:
    await get_project_for_user(db, user, project_id, min_role="editor")
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    if req.tags is not None:
        doc.tags = req.tags
    await db.commit()
    await db.refresh(doc)
    return _to_detail(doc)


async def delete_document(
    db: AsyncSession, user: User, project_id: str, document_id: str
) -> None:
    await get_project_for_user(db, user, project_id, min_role="editor")
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")

    try:
        Path(doc.storage_path).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(f"Failed to delete file {doc.storage_path}: {exc}")

    project_id = str(doc.project_id)
    filename = doc.original_filename
    await db.delete(doc)
    await db.commit()

    from app.services import activity_service
    await activity_service.record(
        db,
        project_id=project_id,
        actor_id=str(user.id),
        action="document_deleted",
        entity_type="document",
        entity_id=document_id,
        summary=f"Deleted {filename}",
    )


async def retry_extraction(
    db: AsyncSession, user: User, project_id: str, document_id: str
) -> DocumentDetail:
    await get_project_for_user(db, user, project_id, min_role="editor")
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")

    doc.extraction_status = "pending"
    doc.extraction_error = None
    await db.commit()
    await db.refresh(doc)

    dispatched = await _dispatch_extraction(doc.id)
    if not dispatched:
        try:
            text = await _extract_async(Path(doc.storage_path))
            doc.extracted_text = text
            doc.extraction_status = "complete"
            doc.extraction_error = None
        except Exception as exc:
            doc.extraction_status = "failed"
            doc.extraction_error = str(exc)
        await db.commit()
        await db.refresh(doc)
    return _to_detail(doc)


async def get_document_for_download(
    db: AsyncSession, user: User, project_id: str, document_id: str
) -> ProjectDocument:
    """Verify ownership and return the document row for FileResponse."""
    await get_project_for_user(db, user, project_id)
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    return doc


async def get_extraction_status(
    db: AsyncSession, user: User, project_id: str, document_id: str
) -> dict:
    """Lightweight poll endpoint — returns just status/error without payload."""
    await get_project_for_user(db, user, project_id)
    doc = (
        await db.execute(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise NotFoundError(f"Document {document_id} not found")
    return {
        "id": str(doc.id),
        "extraction_status": doc.extraction_status,
        "extraction_error": doc.extraction_error,
    }
