from __future__ import annotations

from datetime import datetime, timezone

from app.tasks.celery_app import celery_app, run_async
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(name="export.export_presentation", bind=True, max_retries=2)
def export_presentation_task(self, export_job_id: str):
    """Export a presentation to the requested format."""
    run_async(_run_export(export_job_id))


async def _run_export(export_job_id: str):
    from sqlalchemy import select
    from app.core.database import _session_factory
    from app.models.export_job import ExportJob
    from app.models.presentation import Presentation
    from app.models.theme import Theme

    async with _session_factory() as db:
        job = (
            await db.execute(select(ExportJob).where(ExportJob.id == export_job_id))
        ).scalar_one_or_none()
        if not job:
            logger.info(f"Export job {export_job_id} not found")
            return

        async def fail(msg: str):
            job.status = "failed"
            job.error_message = msg
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

        try:
            job.status = "processing"
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            await db.commit()

            presentation = (
                await db.execute(select(Presentation).where(Presentation.id == job.presentation_id))
            ).scalar_one_or_none()
            if not presentation:
                await fail("Presentation not found")
                return

            theme = (
                await db.execute(select(Theme).where(Theme.id == presentation.theme_id))
            ).scalar_one_or_none()
            if not theme:
                await fail("Theme not found")
                return

            job.progress = 30
            await db.commit()

            fmt = job.format
            out_path = None

            if fmt == "html":
                from app.agents.export.html_export_agent import HtmlExportAgent
                agent = HtmlExportAgent()
                out_path = await agent.run(presentation, theme)

            elif fmt == "pptx":
                from app.agents.export.pptx_export_agent import PptxExportAgent
                agent = PptxExportAgent()
                out_path = await agent.run(presentation, theme)

            elif fmt == "pdf":
                from app.agents.export.pdf_export_agent import PdfExportAgent
                agent = PdfExportAgent()
                out_path = await agent.run(presentation, theme)

            else:
                await fail(f"Unsupported format: {fmt}")
                return

            job.progress = 90
            await db.commit()

            file_size = out_path.stat().st_size if out_path and out_path.exists() else None

            job.status = "completed"
            job.progress = 100
            job.file_path = str(out_path)
            job.file_size = file_size
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Export job {export_job_id} completed: {out_path}")

        except Exception as exc:
            await fail(str(exc))
            raise
