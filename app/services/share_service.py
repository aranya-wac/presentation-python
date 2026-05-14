from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.export.html_export_agent import HtmlExportAgent
from app.core.exceptions import NotFoundError
from app.models.presentation import Presentation
from app.models.theme import Theme


async def render_shared_html(db: AsyncSession, presentation_id: str) -> str:
    presentation = (
        await db.execute(select(Presentation).where(Presentation.id == presentation_id))
    ).scalar_one_or_none()
    if not presentation:
        raise NotFoundError("Presentation not found")

    theme = (
        await db.execute(select(Theme).where(Theme.id == presentation.theme_id))
    ).scalar_one_or_none()
    if not theme:
        raise NotFoundError("Theme not found")

    agent = HtmlExportAgent()
    template = agent._env.get_template("reveal.html.j2")

    colors = theme.colors
    fonts = theme.fonts
    slides_data = []
    for slide in sorted(presentation.slides or [], key=lambda s: s.get("order", 0)):
        blocks_data = []
        for block in slide.get("blocks", []):
            blocks_data.append({
                "id": block.get("id", ""),
                "type": block.get("type", "text"),
                "content": block.get("content", ""),
                "position": block.get("position", {"x": 0, "y": 0, "w": 100, "h": 100}),
                "styling": block.get("styling", {
                    "font_family": "",
                    "font_size": 16,
                    "font_weight": 400,
                    "color": "#000000",
                    "background_color": "transparent",
                    "text_align": "left",
                }),
            })
        bg = slide.get("background") or {}
        bg_type = bg.get("type", "color")
        bg_value = bg.get("value", colors.get("background", "#ffffff"))
        if bg_type == "gradient":
            bg_css = bg_value
        elif bg_type == "image":
            bg_css = f"url({bg_value}) center/cover no-repeat"
        else:
            bg_css = bg_value
        slides_data.append({
            "order": slide.get("order", 0),
            "type": slide.get("type", "content"),
            "background_css": bg_css,
            "blocks": blocks_data,
        })

    return template.render(
        presentation=presentation,
        slides=slides_data,
        theme=theme,
        colors=colors,
        fonts=fonts,
    )
