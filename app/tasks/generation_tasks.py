from __future__ import annotations

from datetime import datetime, timezone

from app.tasks.celery_app import celery_app, run_async
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(name="generation.generate_presentation", bind=True, max_retries=2)
def generate_presentation_task(self, job_id: str, logo_url: str | None = None):
    """Full generation pipeline for a presentation."""
    run_async(_run_generation(job_id, logo_url))


async def _run_generation(job_id: str, logo_url: str | None):
    from sqlalchemy import select
    from app.core.database import _session_factory
    from app.models.generation_job import GenerationJob
    from app.models.presentation import Presentation
    from app.agents.generation.content_analyzer_agent import ContentAnalyzerAgent
    from app.agents.generation.template_mapper_agent import TemplateMappingAgent
    from app.agents.generation.outline_agent import OutlineAgent
    from app.agents.generation.slide_generator_agent import SlideGeneratorAgent
    from app.ai.gemini_client import get_last_token_count

    async with _session_factory() as db:
        job = (
            await db.execute(select(GenerationJob).where(GenerationJob.job_id == job_id))
        ).scalar_one_or_none()
        if not job:
            logger.info(f"Job {job_id} not found, aborting")
            return

        async def set_progress(pct: int):
            job.progress = pct
            await db.commit()

        async def fail(msg: str):
            job.status = "failed"
            job.error_message = msg
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Job {job_id} failed: {msg}")

        try:
            job.status = "processing"
            job.started_at = datetime.now(timezone.utc)
            job.progress = 10
            await db.commit()

            # 10% → 20%: content extraction & analysis
            analyzer = ContentAnalyzerAgent()
            analysis = await analyzer.run(job.file_path)
            token_count = get_last_token_count()
            await set_progress(20)

            # 20% → 30%: template mapping
            mapper = TemplateMappingAgent()
            mapping = await mapper.run(str(job.template_id))
            await set_progress(30)

            # 30% → 50%: outline generation (Phase 1)
            job.gemini_response = analysis
            await db.commit()
            outline_agent = OutlineAgent()
            outline = await outline_agent.run(analysis)
            token_count += get_last_token_count()
            await set_progress(50)

            # 50% → 80%: per-slide content generation in parallel (Phase 2)
            generator = SlideGeneratorAgent()
            slides = await generator.run(outline, analysis, mapping, logo_url or "")
            token_count += get_last_token_count()
            await set_progress(80)

            # 80% → 90%: persist presentation
            theme = mapping.theme

            presentation = Presentation(
                user_id=job.user_id,
                template_id=job.template_id,
                theme_id=theme.id,
                title=analysis.get("title", "My Presentation"),
                description=analysis.get("summary", ""),
                logo_url=logo_url or "",
                slides=slides,
                is_preview=False,
                token_count=token_count,
            )
            db.add(presentation)
            await db.commit()
            await db.refresh(presentation)
            await set_progress(90)

            # 90% → 100%: finalize
            job.status = "completed"
            job.progress = 100
            job.result_presentation_id = str(presentation.id)
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Job {job_id} completed. Presentation: {presentation.id}")

        except Exception as exc:
            await fail(str(exc))
            raise
