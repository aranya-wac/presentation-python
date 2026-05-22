"""
Flow renderer — turns a flow `Card[]` deck into HTML.

A flow card is a tree of blocks (`stack`/`row`/`columns` containers; leaf blocks
hold `content`/`props`). Colours come from the deck's theme. This module mirrors
the frontend `CardRenderer` closely enough for export and share to look right.
It is the single source of truth for HTML export, PDF export, and Share.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

# 16:9 design canvas — matches the frontend renderer.
_DESIGN_W = 1280
_DESIGN_H = 720


def _esc(text: Any) -> str:
    return _html.escape(str(text if text is not None else ""))


def _palette(theme: dict) -> dict:
    colors = (theme or {}).get("colors") or {}
    text = colors.get("text") or "#dfe5ee"
    return {
        "heading": colors.get("primary") or text,
        "text": text,
        "muted": _rgba(text, 0.78),
        "accent": colors.get("accent") or "#6ea8ff",
        "bg": colors.get("background") or "#10131c",
    }


def _rgba(hex_color: str, alpha: float) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return f"rgba(127,127,127,{alpha})"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return f"rgba(127,127,127,{alpha})"
    return f"rgba({r},{g},{b},{alpha})"


def _rich(text: str) -> str:
    """Escape, then parse **bold** and *italic* into <strong>/<em>."""
    out = _esc(text)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def _style_css(style: dict, pal: dict) -> str:
    """Translate a FlowStyle dict into an inline CSS string."""
    s = style or {}
    css: list[str] = []
    if s.get("fontSize") is not None:
        css.append(f"font-size:{s['fontSize']}px")
    if s.get("fontWeight") is not None:
        css.append(f"font-weight:{s['fontWeight']}")
    if s.get("lineHeight") is not None:
        css.append(f"line-height:{s['lineHeight']}")
    if s.get("textAlign"):
        css.append(f"text-align:{s['textAlign']}")
    if s.get("color"):
        css.append(f"color:{s['color']}")
    if s.get("padding") is not None:
        css.append(f"padding:{s['padding']}px")
    if s.get("borderRadius") is not None:
        css.append(f"border-radius:{s['borderRadius']}px")
    if s.get("minHeight") is not None:
        css.append(f"min-height:{s['minHeight']}px")
    return ";".join(css)


def _container_css(block: dict) -> str:
    s = block.get("style") or {}
    t = block.get("type")
    align_map = {"start": "flex-start", "center": "center", "end": "flex-end",
                 "stretch": "stretch", "between": "space-between"}
    css = [
        "display:flex",
        f"flex-direction:{'column' if t == 'stack' else 'row'}",
        f"gap:{s.get('gap', 24)}px",
        f"align-items:{align_map.get(s.get('align', 'stretch'), 'stretch')}",
        f"justify-content:{align_map.get(s.get('justify', 'start'), 'flex-start')}",
        "min-width:0",
    ]
    if s.get("padding") is not None:
        css.append(f"padding:{s['padding']}px")
    if s.get("flex") is not None:
        css.append(f"flex:{s['flex']} 1 0")
    return ";".join(css)


def _leaf_flex(block: dict) -> str:
    s = block.get("style") or {}
    return f"flex:{s['flex']} 1 0;" if s.get("flex") is not None else ""


def _render_block(block: dict, pal: dict) -> str:
    """Render one FlowBlock node to HTML."""
    if not isinstance(block, dict):
        return ""
    t = block.get("type")
    s = block.get("style") or {}

    if t in ("stack", "row", "columns"):
        children = "".join(_render_block(c, pal) for c in (block.get("children") or []))
        return f'<div style="{_container_css(block)}">{children}</div>'

    base = _style_css(s, pal)
    content = block.get("content") or ""

    if t == "heading":
        css = f"font-weight:800;line-height:1.15;font-size:34px;color:{pal['heading']};{base}"
        return f'<div style="{css}">{_rich(content)}</div>'
    if t == "text":
        css = f"line-height:1.55;font-size:20px;color:{pal['muted']};{base}"
        return f'<div style="{css}">{_rich(content)}</div>'
    if t == "bullets":
        items = "".join(f"<li>{_rich(line)}</li>" for line in content.split("\n") if line.strip())
        css = f"margin:0;padding-left:24px;font-size:20px;line-height:1.5;color:{pal['text']};{base}"
        return f'<ul style="{css}">{items}</ul>'
    if t == "quote":
        css = f"font-size:26px;font-style:italic;line-height:1.4;color:{pal['heading']};{base}"
        return f'<div style="{css}">{_rich(content)}</div>'
    if t == "stat":
        nl = content.find("\n")
        value = content[:nl] if nl >= 0 else content
        label = content[nl + 1:] if nl >= 0 else ""
        return (
            f'<div style="display:flex;flex-direction:column;gap:4px;{_leaf_flex(block)}">'
            f'<div style="font-size:46px;font-weight:800;line-height:1.1;color:{pal["heading"]}">{_rich(value)}</div>'
            f'<div style="font-size:15px;color:{pal["muted"]}">{_rich(label)}</div></div>'
        )
    if t == "callout":
        nl = content.find("\n")
        title = content[:nl] if nl >= 0 else content
        body = content[nl + 1:] if nl >= 0 else ""
        css = (f"display:flex;flex-direction:column;gap:6px;padding:{s.get('padding', 22)}px;"
               f"background:{_rgba(pal['accent'], 0.12)};border-radius:{s.get('borderRadius', 14)}px;"
               f"min-width:0;{_leaf_flex(block)}")
        return (
            f'<div style="{css}">'
            f'<div style="font-weight:700;font-size:19px;color:{pal["heading"]}">{_rich(title)}</div>'
            f'<div style="font-size:15px;line-height:1.5;color:{pal["muted"]}">{_rich(body)}</div></div>'
        )
    if t == "image":
        src = _esc((block.get("props") or {}).get("src") or "")
        css = f"width:100%;object-fit:cover;border-radius:{s.get('borderRadius', 12)}px;display:block;{_leaf_flex(block)}"
        if s.get("minHeight"):
            css += f";min-height:{s['minHeight']}px"
        return f'<img src="{src}" alt="" style="{css}"/>'
    if t == "table":
        td = (block.get("props") or {}).get("tableData") or {}
        headers = td.get("headers") or []
        rows = td.get("rows") or []
        head = "".join(
            f'<th style="text-align:left;padding:10px 14px;font-weight:700;'
            f'color:{pal["heading"]};border-bottom:2px solid {pal["accent"]}">{_rich(h)}</th>'
            for h in headers
        )
        body = "".join(
            "<tr>" + "".join(
                f'<td style="padding:10px 14px;border-bottom:1px solid {_rgba(pal["text"], 0.16)}">{_rich(cell)}</td>'
                for cell in row
            ) + "</tr>"
            for row in rows
        )
        css = f"width:100%;border-collapse:collapse;font-size:16px;color:{pal['text']};{base}"
        return f'<table style="{css}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    if t == "chart":
        props = block.get("props") or {}
        data = props.get("chartData") or []
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:16px;'
            f'padding:6px 0;border-bottom:1px solid {_rgba(pal["text"], 0.14)}">'
            f'<span style="color:{pal["muted"]}">{_esc(d.get("label"))}</span>'
            f'<span style="color:{pal["heading"]};font-weight:700">{_esc(d.get("value"))}</span></div>'
            for d in data if isinstance(d, dict)
        )
        return f'<div style="font-size:16px;{_leaf_flex(block)}">{rows}</div>'
    if t == "divider":
        return f'<div style="height:1px;background:{_rgba(pal["text"], 0.2)}"></div>'
    if t == "spacer":
        return f'<div style="height:{s.get("minHeight", 24)}px"></div>'
    if t == "icon":
        return f'<div style="width:48px;height:48px;border-radius:50%;background:{_rgba(pal["accent"], 0.16)}"></div>'
    return ""


def render_card_html(card: dict, theme: dict) -> str:
    """Render one flow card to a self-contained 16:9 HTML fragment."""
    pal = _palette(theme)
    bg = card.get("background") or {}
    bg_type = bg.get("type")
    if bg_type == "image":
        surface = (f'background-image:url({_esc(bg.get("value"))});'
                   f'background-size:cover;background-position:center')
    elif bg_type == "gradient":
        surface = f'background-image:{_esc(bg.get("value"))}'
    else:
        surface = f'background:{pal["bg"]}'
    overlay = ""
    if bg_type == "image":
        overlay = (f'<div style="position:absolute;inset:0;'
                   f'background:{_rgba(pal["bg"], 0.62)}"></div>')
    inner = _render_block(card.get("root") or {}, pal)
    return (
        f'<div data-flow-card style="position:relative;width:{_DESIGN_W}px;'
        f'min-height:{_DESIGN_H}px;overflow:hidden;color:{pal["text"]};'
        f'font-family:Inter,Segoe UI,sans-serif;{surface}">'
        f'{overlay}'
        f'<div style="position:relative;z-index:1">{inner}</div>'
        f'</div>'
    )


def render_deck_html(cards: list, theme: dict, title: str) -> str:
    """Render a whole flow deck to one self-contained HTML document."""
    sections = "".join(
        f'<section style="margin:0 auto 40px;width:{_DESIGN_W}px;'
        f'box-shadow:0 12px 40px rgba(0,0,0,0.4)">{render_card_html(c, theme)}</section>'
        for c in (cards or []) if isinstance(c, dict)
    )
    return (
        "<!DOCTYPE html>"
        f'<html><head><meta charset="utf-8"/><title>{_esc(title)}</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        "</head>"
        '<body style="margin:0;padding:40px 0;background:#0a0c12">'
        f"{sections}"
        "</body></html>"
    )
