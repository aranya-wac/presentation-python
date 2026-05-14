from __future__ import annotations

import json
from typing import Any

from app.models.template import Template
from app.models.theme import Theme
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TemplateMappingResult:
    def __init__(self, template: Template, theme: Theme):
        self.template = template
        self.theme = theme
        meta = template.metadata_json or {}
        self.slide_count = meta.get("total_slides", 0)

    def theme_info(self) -> str:
        t = self.theme
        return json.dumps({
            "name": t.name,
            "colors": t.colors,
            "fonts": t.fonts,
        })

    def template_slides_info(self) -> str:
        return json.dumps(self.template.slides or [], default=str)


class TemplateMappingAgent:
    """Loads template and theme data, prepares mapping context."""

    async def run(self, template_id: str) -> TemplateMappingResult:
        from sqlalchemy import select
        from app.core.database import _session_factory

        async with _session_factory() as db:
            template = (
                await db.execute(select(Template).where(Template.id == template_id))
            ).scalar_one_or_none()
            if not template:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Template {template_id} not found")

            theme = (
                await db.execute(select(Theme).where(Theme.id == template.theme_id))
            ).scalar_one_or_none()
            if not theme:
                from app.core.exceptions import NotFoundError
                raise NotFoundError(f"Theme {template.theme_id} not found")

        logger.info(f"Template '{template.name}' mapped to theme '{theme.name}'")
        return TemplateMappingResult(template=template, theme=theme)
