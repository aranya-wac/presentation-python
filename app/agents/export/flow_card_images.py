"""
Render flow cards to 16:9 PNG images for image-based export (PPTX). Each card's
HTML fragment is rendered with Playwright when available; if Playwright is not
installed, a solid theme-coloured fallback image with the card's heading text is
produced instead so export never fails.
"""
from __future__ import annotations

import re
from io import BytesIO

from app.services.flow_renderer import render_card_html, _palette
from app.utils.logger import get_logger

logger = get_logger(__name__)

_W, _H = 1280, 720


def _first_heading(card: dict) -> str:
    """The card's first heading text, for the fallback image."""
    def walk(node: dict):
        if not isinstance(node, dict):
            return None
        if node.get("type") == "heading" and node.get("content"):
            return re.sub(r"\*", "", str(node["content"]))
        for child in node.get("children") or []:
            hit = walk(child)
            if hit:
                return hit
        return None
    return walk(card.get("root") or {}) or "Slide"


def _fallback_image(card: dict, theme: dict) -> bytes:
    """A solid theme-coloured slide with the heading — used when no renderer."""
    from PIL import Image, ImageDraw

    pal = _palette(theme)
    img = Image.new("RGB", (_W, _H), pal["bg"])
    draw = ImageDraw.Draw(img)
    draw.text((90, _H // 2 - 20), _first_heading(card), fill=pal["heading"])
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _playwright_image(html: str) -> bytes | None:
    """Render an HTML fragment to a 1280x720 PNG via Playwright, or None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        doc = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            "<style>html,body{margin:0;padding:0}</style></head>"
            f"<body>{html}</body></html>"
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": _W, "height": _H})
            page.set_content(doc)
            png = page.screenshot(clip={"x": 0, "y": 0, "width": _W, "height": _H})
            browser.close()
            return png
    except Exception as exc:  # noqa: BLE001 — fall back to the solid image
        logger.warning(f"playwright card render failed: {exc}")
        return None


def render_card_images(cards: list, theme: dict) -> list[bytes]:
    """Render every flow card to a 16:9 PNG (bytes)."""
    images: list[bytes] = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        html = render_card_html(card, theme)
        png = _playwright_image(html) or _fallback_image(card, theme)
        images.append(png)
    return images
