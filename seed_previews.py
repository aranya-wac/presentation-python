"""Pre-generate and cache preview presentations for all templates."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import init_db, close_db
import app.core.database as _db
import app.core.database_models  # noqa: register models


async def main():
    await init_db()
    from sqlalchemy import select
    from app.models.template import Template
    from app.models.theme import Theme
    from app.models.presentation import Presentation
    from app.agents.generation.preview_generator_agent import PreviewGeneratorAgent

    async with _db._session_factory() as db:
        templates = (await db.execute(select(Template).where(Template.is_active == True))).scalars().all()
        print(f"Found {len(templates)} active templates")

        agent = PreviewGeneratorAgent()

        for t in templates:
            # Skip if already cached
            existing = (await db.execute(
                select(Presentation).where(
                    Presentation.template_id == t.id,
                    Presentation.is_preview == True,
                )
            )).scalar_one_or_none()

            if existing:
                print(f"[SKIP] '{t.name}' — preview already cached")
                continue

            theme = (await db.execute(select(Theme).where(Theme.id == t.theme_id))).scalar_one_or_none()
            if not theme:
                print(f"[SKIP] '{t.name}' — theme not found")
                continue

            print(f"[GEN ] '{t.name}' ...", end=" ", flush=True)
            try:
                await agent.run(t, theme)
                print("done")
            except Exception as e:
                print(f"FAILED: {e}")

    await close_db()
    print("All previews seeded.")


if __name__ == "__main__":
    asyncio.run(main())
