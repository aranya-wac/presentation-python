"""
Pre-generate Gamma-style preview presentations for all active templates.

Usage:
    cd backend
    python seeds/generate_previews.py
    python seeds/generate_previews.py --force   # regenerate even if cached
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import close_db, init_db
import app.core.database as _db_module
from app.utils.logger import get_logger

logger = get_logger("generate_previews")


async def main(force: bool = False) -> None:
    import app.core.database_models  # noqa: F401 — registers all ORM models
    await init_db()

    from sqlalchemy import select, delete
    from app.models.template import Template
    from app.models.theme import Theme
    from app.models.presentation import Presentation
    from app.agents.generation.preview_generator_agent import PreviewGeneratorAgent

    async with _db_module._session_factory() as db:
        templates = (await db.execute(select(Template).where(Template.is_active == True))).scalars().all()  # noqa: E712
        logger.info(f"Found {len(templates)} active templates")

        for template in templates:
            theme = (await db.execute(select(Theme).where(Theme.id == template.theme_id))).scalar_one_or_none()
            if not theme:
                logger.info(f"  [{template.name}] — theme not found, skipping")
                continue

            # Check for existing cached preview
            existing = (
                await db.execute(
                    select(Presentation).where(
                        Presentation.template_id == template.id,
                        Presentation.is_preview == True,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()

            if existing and not force:
                logger.info(f"  [{template.name}] — preview already exists (id={existing.id}), skipping")
                continue

            if existing and force:
                logger.info(f"  [{template.name}] — deleting existing preview to regenerate...")
                await db.execute(
                    delete(Presentation).where(Presentation.id == existing.id)
                )
                await db.commit()

            logger.info(f"  [{template.name}] — generating preview ({template.category}, {(template.metadata_json or {}).get('total_slides', '?')} slides)...")
            try:
                agent = PreviewGeneratorAgent()
                preview = await agent.run(template, theme)
                logger.info(f"  [{template.name}] — done (presentation id={preview.id}, {len(preview.slides or [])} slides)")
            except Exception as exc:
                logger.info(f"  [{template.name}] — FAILED: {exc}")

            # Brief pause between templates
            logger.info("  Waiting 5s before next template...")
            await asyncio.sleep(5)

    await close_db()
    logger.info("All previews generated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-generate template previews")
    parser.add_argument("--force", action="store_true", help="Regenerate even if preview already exists")
    args = parser.parse_args()
    asyncio.run(main(force=args.force))
