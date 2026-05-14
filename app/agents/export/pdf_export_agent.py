from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.storage import export_path
from app.models.presentation import Presentation
from app.models.theme import Theme
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PdfExportAgent:
    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )

    async def run(self, presentation: Presentation, theme: Theme) -> Path:
        template = self._env.get_template("pdf.html.j2")

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
            slides_data.append({
                "order": slide.get("order", 0),
                "type": slide.get("type", "content"),
                "blocks": blocks_data,
                "notes": (slide.get("notes") or "").strip(),
            })

        colors = theme.colors  # dict
        fonts = theme.fonts    # dict

        html_content = template.render(
            presentation=presentation,
            slides=slides_data,
            theme=theme,
            colors=colors,
            fonts=fonts,
        )

        # Write temp HTML
        tmp_html = export_path(str(presentation.id), "pdf").with_suffix(".tmp.html")
        tmp_html.write_text(html_content, encoding="utf-8")

        out_path = export_path(str(presentation.id), "pdf")

        try:
            from weasyprint import HTML
            HTML(filename=str(tmp_html)).write_pdf(str(out_path))
            logger.info(f"PDF export saved to {out_path}")
        except Exception as exc:
            logger.info(f"WeasyPrint failed: {exc}. Saving HTML as fallback.")
            import shutil
            shutil.copy(str(tmp_html), str(out_path))
        finally:
            tmp_html.unlink(missing_ok=True)

        return out_path
