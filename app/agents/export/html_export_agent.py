from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.storage import export_path
from app.models.presentation import Presentation
from app.models.theme import Theme
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class HtmlExportAgent:
    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )

    async def run(self, presentation: Presentation, theme: Theme) -> Path:
        template = self._env.get_template("reveal.html.j2")

        colors = theme.colors  # dict: {primary, secondary, accent, background, text}
        fonts = theme.fonts    # dict: {heading, body, caption} each {family, size, weight}

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
            bg_type  = bg.get("type", "color")
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
                "notes": (slide.get("notes") or "").strip(),
            })

        html_content = template.render(
            presentation=presentation,
            slides=slides_data,
            theme=theme,
            colors=colors,
            fonts=fonts,
        )

        out_path = export_path(str(presentation.id), "html")
        out_path.write_text(html_content, encoding="utf-8")
        logger.info(f"HTML export saved to {out_path}")
        return out_path
