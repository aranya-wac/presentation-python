"""Celery task for document text extraction.

Extraction runs in a worker so large PDFs/DOCX don't block the API request.
A sync fallback in `document_service.upload_document` keeps dev workflow
working without a running Celery worker (set `EXTRACTION_SYNC_FALLBACK=true`).
"""
from __future__ import annotations

from pathlib import Path

from app.tasks.celery_app import celery_app, run_async
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(name="documents.extract_text", bind=True, max_retries=2)
def extract_document_text_task(self, document_id: str) -> None:
    """Extract text from a stored document and update its row."""
    run_async(_extract(document_id))


async def _extract(document_id: str) -> None:
    import asyncio

    from sqlalchemy import select

    from app.core.database import _session_factory
    from app.extractors.extractor_factory import extract_content
    from app.models.project import ProjectDocument

    async with _session_factory() as db:
        doc = (
            await db.execute(
                select(ProjectDocument).where(ProjectDocument.id == document_id)
            )
        ).scalar_one_or_none()
        if not doc:
            logger.warning(f"Extraction task: document {document_id} not found")
            return

        try:
            text = await asyncio.to_thread(extract_content, Path(doc.storage_path))
            doc.extracted_text = text
            doc.extraction_status = "complete"
            doc.extraction_error = None
        except Exception as exc:
            logger.warning(f"Extraction failed for {doc.storage_path}: {exc}")
            doc.extraction_status = "failed"
            doc.extraction_error = str(exc)
        await db.commit()
