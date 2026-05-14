from __future__ import annotations

import asyncio
import json
from typing import Any

from app.ai import gemini_client
from app.ai.prompt_templates import SLIDE_CONTENT_PROMPT, render
from app.agents.generation.template_mapper_agent import TemplateMappingResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

W, H = 1280, 720


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _premium_card_pair(theme_colors: dict) -> tuple[str, str, str, str]:
    """Return (dark_bg, dark_text, light_bg, light_text) for alternating cards."""
    primary = theme_colors.get("primary", "#0F172A")
    surface = theme_colors.get("surface", "#F1F5F9")
    text_col = theme_colors.get("text", "#0F172A")
    return primary, "#ffffff", surface, text_col


def _is_card_dark(idx: int) -> bool:
    """Gamma-style: top-left and bottom-right are dark, others light."""
    return idx in (0, 3)


def _layout_blocks(
    slide_type: str,
    slide_layout: str,
    gen_blocks: list[dict],
    theme_colors: dict,
    theme_fonts: dict,
) -> list[dict]:
    primary   = theme_colors.get("primary",    "#0F172A")
    secondary = theme_colors.get("secondary",  "#1E293B")
    accent    = theme_colors.get("accent",     "#6366F1")
    bg_col    = theme_colors.get("background", "#FFFFFF")
    text_col  = theme_colors.get("text",       "#0F172A")
    surface   = theme_colors.get("surface",    "#F1F5F9")

    hfam = theme_fonts.get("heading", {}).get("family", "Inter, sans-serif")
    bfam = theme_fonts.get("body",    {}).get("family", "Inter, sans-serif")

    dark_card_bg, dark_card_text, light_card_bg, light_card_text = _premium_card_pair(theme_colors)

    blocks_out: list[dict] = []

    # ── Block factory ──────────────────────────────────────────────────────
    def _b(bid, btype, content, x, y, w, h, *,
           size=16, weight=400, color=None, align="left", family=None, bg="transparent"):
        return {
            "id": bid, "type": btype, "content": content,
            "position": {"x": x, "y": y, "w": w, "h": h},
            "styling": {
                "font_family": family or bfam,
                "font_size": size, "font_weight": weight,
                "color": color or text_col,
                "background_color": bg, "text_align": align,
            },
        }

    def _badge(label: str, x: int = 60, y: int = 35) -> dict | None:
        if not label:
            return None
        return {
            "id": "badge", "type": "badge", "content": label,
            "position": {"x": x, "y": y, "w": max(140, len(label) * 9 + 32), "h": 28},
            "styling": {
                "font_family": bfam, "font_size": 11, "font_weight": 700,
                "color": accent, "background_color": "transparent", "text_align": "left",
            },
        }

    def _accent_bar(x: int, y: int, w: int = 80, h: int = 4) -> dict:
        return {
            "id": f"accent-{x}-{y}", "type": "shape", "content": "",
            "position": {"x": x, "y": y, "w": w, "h": h},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": accent, "background_color": accent, "text_align": "left",
            },
        }

    def _panel(bid: str, x: int, y: int, w: int, h: int, gradient: str) -> dict:
        """Full gradient decorative panel — Gamma's image-panel replacement."""
        return {
            "id": bid, "type": "panel", "content": "",
            "position": {"x": x, "y": y, "w": w, "h": h},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": "transparent", "background_color": gradient, "text_align": "left",
            },
        }

    # ── Index gen_blocks by type ───────────────────────────────────────────
    by_type: dict[str, list[dict]] = {}
    for gb in gen_blocks:
        by_type.setdefault(gb.get("type", "body"), []).append(gb)

    def _get(btype: str, fallback: str = "") -> str:
        lst = by_type.get(btype, [])
        if lst:
            b = lst.pop(0)
            items = b.get("items", [])
            return "\n".join(items) if items else b.get("content", fallback)
        return fallback

    def _bullets() -> list[str]:
        out: list[str] = []
        for gb in gen_blocks:
            if gb.get("type") in ("bullet", "body"):
                items = gb.get("items", [])
                if items:
                    out.extend(items)
                elif gb.get("content"):
                    out.append(gb["content"])
        return out

    BADGE_LABELS = {
        "agenda":     "OVERVIEW",
        "content":    "KEY INSIGHTS",
        "stats":      "METRICS",
        "quote":      "PERSPECTIVE",
        "chart":      "DATA",
        "roadmap":    "ROADMAP",
        "comparison": "COMPARISON",
        "kanban":     "FRAMEWORK",
        "funnel":     "FUNNEL",
    }
    badge_label = BADGE_LABELS.get(slide_type, "KEY INSIGHTS")

    layout = slide_layout or ""

    # ── Premium chrome: thin accent strip on the left edge of every content
    # slide (not on title/closing — they have full hero treatment). Subtle
    # but instantly elevates the deck.
    if slide_type not in ("title", "closing") and layout not in ("title_hero", "closing"):
        blocks_out.append({
            "id": "edge-accent", "type": "shape", "content": "",
            "position": {"x": 0, "y": 0, "w": 4, "h": H},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": accent, "background_color": accent, "text_align": "left",
            },
        })

    # ── title_hero ──────────────────────────────────────────────────────────
    if slide_type == "title" or layout == "title_hero":
        title_text = _get("title", "Presentation Title")
        sub_text   = _get("subtitle", "")

        # Adaptive title sizing — long titles need a smaller font and more
        # vertical room or they overflow the slide.
        title_len = len(title_text)
        if   title_len <= 22:  t_size, t_height, t_y = 84, 200, 230
        elif title_len <= 40:  t_size, t_height, t_y = 64, 220, 220
        elif title_len <= 60:  t_size, t_height, t_y = 52, 260, 200
        elif title_len <= 90:  t_size, t_height, t_y = 42, 300, 180
        else:                  t_size, t_height, t_y = 34, 340, 160

        # Subtitle / accent bar / footer positions shift with title height.
        bar_y = t_y + t_height + 10
        sub_y = bar_y + 20

        # Top-of-slide eyebrow tag — small, all-caps, accent color. Adds the
        # editorial chrome Gamma uses on hero slides.
        blocks_out.append(_b("hero-eyebrow", "badge", "PRESENTATION",
                              100, 80, 200, 28,
                              size=11, weight=800, color=accent, align="left", family=bfam))
        # Decorative thin line under the eyebrow
        blocks_out.append({
            "id": "hero-eyebrow-line", "type": "shape", "content": "",
            "position": {"x": 100, "y": 116, "w": 60, "h": 2},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": accent, "background_color": accent, "text_align": "left",
            },
        })

        blocks_out.append(_b("title", "title", title_text,
                              100, t_y, 1080, t_height,
                              size=t_size, weight=900, color="#ffffff",
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(100, bar_y, 120, 5))
        if sub_text:
            blocks_out.append(_b("subtitle", "subtitle", sub_text,
                                  100, sub_y, 1080, 90,
                                  size=22, weight=400,
                                  color="rgba(255,255,255,0.78)", align="left"))

        # Bottom-left brand mark — subtle but signals polish.
        blocks_out.append(_b("hero-foot", "caption", "Crafted with WAC Deck Studio",
                              100, H - 60, 600, 24,
                              size=11, weight=600,
                              color="rgba(255,255,255,0.45)", align="left", family=bfam))

    # ── agenda_rows ─────────────────────────────────────────────────────────
    elif slide_type == "agenda" or layout == "agenda_rows":
        heading = _get("heading", _get("title", "Agenda"))
        bullets  = _bullets()

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 900, 80,
                              size=44, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        for idx, b in enumerate(bullets[:6]):
            clean = b.lstrip("•-* ").strip()
            cy = 178 + idx * 82
            is_dark = idx % 2 == 0
            card_bg   = dark_card_bg   if is_dark else light_card_bg
            card_text = dark_card_text if is_dark else light_card_text
            blocks_out.append({
                "id": f"agenda-row-{idx}", "type": "card",
                "content": f"{idx + 1:02d}  {clean}",
                "position": {"x": 60, "y": cy, "w": 1160, "h": 68},
                "styling": {
                    "font_family": hfam, "font_size": 20, "font_weight": 600,
                    "color": card_text, "background_color": card_bg, "text_align": "left",
                },
            })

    # ── split_panel (Gamma hero: gradient panel left + cards right) ─────────
    elif layout == "split_panel":
        heading     = _get("heading", _get("title", "Section"))
        bullets_raw = _bullets()

        panel_grad = f"linear-gradient(160deg, {primary} 0%, {secondary} 65%, {accent}55 100%)"
        blocks_out.append(_panel("left-panel", 0, 0, 490, 720, panel_grad))
        # Accent dot on panel
        blocks_out.append({
            "id": "panel-dot", "type": "shape", "content": "",
            "position": {"x": 420, "y": 320, "w": 40, "h": 40},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": accent, "background_color": f"{accent}60", "text_align": "left",
            },
        })

        rx = 530
        rw = 710

        bdg = _badge(badge_label, rx, 35)
        if bdg:
            blocks_out.append(bdg)

        blocks_out.append(_b("heading", "heading", heading,
                              rx, 75, rw, 100,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))

        card_w = (rw - 20) // 2   # 345
        card_h = 185

        for idx, btext in enumerate(bullets_raw[:4]):
            parts = btext.split(": ", 1) if ": " in btext else [btext, ""]
            title_line = parts[0]
            body_line  = parts[1] if len(parts) > 1 else ""
            content_str = f"{title_line}\n{body_line}" if body_line else title_line

            col = idx % 2
            row = idx // 2
            cx  = rx + col * (card_w + 20)
            cy  = 195 + row * (card_h + 15)

            is_dark   = _is_card_dark(idx)
            card_bg   = dark_card_bg   if is_dark else light_card_bg
            card_text = dark_card_text if is_dark else light_card_text

            blocks_out.append({
                "id": f"card-{idx}", "type": "card", "content": content_str,
                "position": {"x": cx, "y": cy, "w": card_w, "h": card_h},
                "styling": {
                    "font_family": hfam, "font_size": 17, "font_weight": 700,
                    "color": card_text, "background_color": card_bg, "text_align": "left",
                },
            })

        if not bullets_raw:
            body_text = _get("body", "")
            blocks_out.append(_b("body", "bullet", body_text,
                                  rx, 195, rw, 460,
                                  size=22, weight=400, color=text_col))

    # ── card_grid (full-width 2×2) ────────────────────────────────────────
    elif layout == "card_grid":
        heading     = _get("heading", _get("title", "Overview"))
        bullets_raw = _bullets()

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=44, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        card_w, card_h = 570, 210

        for idx, btext in enumerate(bullets_raw[:4]):
            col = idx % 2
            row = idx // 2
            cx  = 60  + col * (card_w + 40)
            cy  = 178 + row * (card_h + 20)

            is_dark   = _is_card_dark(idx)
            card_bg   = dark_card_bg   if is_dark else light_card_bg
            card_text = dark_card_text if is_dark else light_card_text

            blocks_out.append({
                "id": f"card-{idx}", "type": "card", "content": btext,
                "position": {"x": cx, "y": cy, "w": card_w, "h": card_h},
                "styling": {
                    "font_family": hfam, "font_size": 20, "font_weight": 700,
                    "color": card_text, "background_color": card_bg, "text_align": "left",
                },
            })

    # ── stats_showcase ────────────────────────────────────────────────────
    elif slide_type == "stats" or layout == "stats_showcase":
        heading = _get("heading", _get("title", "Key Metrics"))
        stats   = by_type.get("stat", [])
        if not stats:
            stats = [{"id": f"s{i}", "content": f"Metric {i+1}"} for i in range(3)]

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=44, weight=800, color="#ffffff",
                              align="center", family=hfam))
        blocks_out.append(_accent_bar(560, 158, 160, 5))

        n = min(len(stats), 4)
        if n <= 2:
            stat_w = 460
            total  = n * stat_w + (n - 1) * 40
            sx0    = (W - total) // 2
            for idx, stat in enumerate(stats[:n]):
                sx = sx0 + idx * (stat_w + 40)
                blocks_out.append({
                    "id": stat.get("id", f"stat-{idx}"), "type": "stat",
                    "content": stat.get("content", "—"),
                    "position": {"x": sx, "y": 220, "w": stat_w, "h": 290},
                    "styling": {
                        "font_family": hfam, "font_size": 72, "font_weight": 800,
                        "color": accent,
                        "background_color": "rgba(255,255,255,0.08)", "text_align": "center",
                    },
                })
        else:
            stat_w, stat_h = 560, 210
            for idx, stat in enumerate(stats[:4]):
                col = idx % 2
                row = idx // 2
                sx  = 60  + col * (stat_w + 40)
                sy  = 180 + row * (stat_h + 20)
                blocks_out.append({
                    "id": stat.get("id", f"stat-{idx}"), "type": "stat",
                    "content": stat.get("content", "—"),
                    "position": {"x": sx, "y": sy, "w": stat_w, "h": stat_h},
                    "styling": {
                        "font_family": hfam, "font_size": 60, "font_weight": 800,
                        "color": accent,
                        "background_color": "rgba(255,255,255,0.08)", "text_align": "center",
                    },
                })

    # ── quote_centered ─────────────────────────────────────────────────────
    elif slide_type == "quote" or layout == "quote_centered":
        quote_text  = _get("quote",   _get("body", "Inspiring words go here."))
        attribution = _get("caption", "— Author")

        blocks_out.append(_b("quote-mark", "text", "“",
                              80, 50, 200, 140,
                              size=160, weight=900, color=accent,
                              align="left", family=hfam))
        blocks_out.append(_b("quote-text", "quote", quote_text,
                              80, 175, 1120, 330,
                              size=32, weight=400, color="#ffffff",
                              align="center", family=hfam))
        blocks_out.append(_b("attribution", "caption", attribution,
                              80, 530, 1120, 50,
                              size=18, weight=600, color=accent, align="center"))

    # ── chart_showcase ────────────────────────────────────────────────────
    elif slide_type == "chart" or layout == "chart_showcase":
        heading = _get("heading", _get("title", "Key Data"))
        chart_blocks = by_type.get("chart", [])
        caption_text = _get("caption", "")

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        chart_y = 190
        chart_h = 440 if caption_text else 480
        if chart_blocks:
            cb = chart_blocks[0]
            blocks_out.append({
                "id": cb.get("id", "chart-0"),
                "type": "chart",
                "content": "",
                "chart_type": cb.get("chart_type", "bar"),
                "chart_data": cb.get("chart_data", []),
                "position": {"x": 80, "y": chart_y, "w": 1120, "h": chart_h},
                "styling": {
                    "font_family": bfam, "font_size": 14, "font_weight": 400,
                    "color": text_col,
                    "background_color": "rgba(15,23,42,0.04)",
                    "text_align": "center",
                },
            })
        else:
            blocks_out.append(_b("chart-empty", "text", "No chart data available",
                                  80, chart_y, 1120, chart_h,
                                  size=16, color=text_col, align="center"))

        if caption_text:
            blocks_out.append(_b("caption", "caption", caption_text,
                                  80, chart_y + chart_h + 10, 1120, 40,
                                  size=14, weight=400,
                                  color=text_col, align="center", family=bfam))

    # ── roadmap_timeline ──────────────────────────────────────────────────
    elif slide_type == "roadmap" or layout == "roadmap_timeline":
        heading = _get("heading", _get("title", "Roadmap"))
        steps = by_type.get("roadmap_step", [])

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        n = min(len(steps), 6) if steps else 0
        if n == 0:
            blocks_out.append(_b("roadmap-empty", "text", "No roadmap data",
                                  80, 220, 1120, 200,
                                  size=16, color=text_col, align="center"))
        else:
            # Horizontal timeline: circles + labels + connecting line
            margin_x = 80
            avail_w  = W - 2 * margin_x
            step_w   = avail_w // n
            circle_d = 64
            track_y  = 290
            blocks_out.append({
                "id": "rm-track", "type": "shape", "content": "",
                "position": {"x": margin_x + step_w // 2, "y": track_y + circle_d // 2 - 2,
                             "w": (n - 1) * step_w if n > 1 else 1, "h": 3},
                "styling": {
                    "font_family": "", "font_size": 0, "font_weight": 0,
                    "color": accent, "background_color": f"{accent}55", "text_align": "left",
                },
            })
            for idx, step in enumerate(steps[:n]):
                raw = step.get("content", "")
                phase, _, label = raw.partition("||")
                cx = margin_x + idx * step_w + step_w // 2 - circle_d // 2
                blocks_out.append({
                    "id": f"rm-circle-{idx}", "type": "process_circle",
                    "content": str(idx + 1),
                    "position": {"x": cx, "y": track_y, "w": circle_d, "h": circle_d},
                    "styling": {
                        "font_family": hfam, "font_size": 26, "font_weight": 800,
                        "color": "#ffffff", "background_color": accent, "text_align": "center",
                    },
                })
                blocks_out.append(_b(f"rm-phase-{idx}", "text", phase,
                                      margin_x + idx * step_w, track_y + circle_d + 20,
                                      step_w, 30,
                                      size=14, weight=700, color=accent, align="center", family=hfam))
                blocks_out.append(_b(f"rm-label-{idx}", "text", label,
                                      margin_x + idx * step_w + 8, track_y + circle_d + 56,
                                      step_w - 16, 110,
                                      size=14, weight=500, color=text_col, align="center", family=bfam))

    # ── comparison_split ──────────────────────────────────────────────────
    elif slide_type == "comparison" or layout == "comparison_split":
        heading = _get("heading", _get("title", "Comparison"))
        left_blocks  = by_type.get("comparison_left",  [])
        right_blocks = by_type.get("comparison_right", [])

        # First item carries the side label ("Before"/"After") before "||".
        def _split_label_and_items(side_blocks: list[dict]) -> tuple[str, list[str]]:
            if not side_blocks:
                return "", []
            first = side_blocks[0].get("content", "")
            label, _, first_item = first.partition("||")
            items = [first_item] if first_item else []
            for b in side_blocks[1:]:
                content = b.get("content", "")
                _, _, item_text = content.partition("||")
                if item_text:
                    items.append(item_text)
            return label, items

        left_label,  left_items  = _split_label_and_items(left_blocks)
        right_label, right_items = _split_label_and_items(right_blocks)
        left_label  = left_label  or "Before"
        right_label = right_label or "After"

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        col_w = 540
        gap = 40
        col_x_left  = 60
        col_x_right = col_x_left + col_w + gap
        col_y       = 190
        col_h       = 460

        # Left column (light treatment)
        blocks_out.append(_panel("cmp-left-bg", col_x_left, col_y, col_w, col_h, surface))
        blocks_out.append(_b("cmp-left-label", "badge", left_label,
                              col_x_left + 24, col_y + 22, col_w - 48, 28,
                              size=12, weight=800, color=text_col, align="left", family=bfam))
        for i, item in enumerate(left_items[:5]):
            blocks_out.append(_b(f"cmp-left-{i}", "bullet", f"• {item}",
                                  col_x_left + 24, col_y + 70 + i * 64, col_w - 48, 56,
                                  size=16, weight=500, color=text_col, align="left", family=bfam))

        # Right column (dark treatment for contrast)
        blocks_out.append(_panel("cmp-right-bg", col_x_right, col_y, col_w, col_h, primary))
        blocks_out.append(_b("cmp-right-label", "badge", right_label,
                              col_x_right + 24, col_y + 22, col_w - 48, 28,
                              size=12, weight=800, color=accent, align="left", family=bfam))
        for i, item in enumerate(right_items[:5]):
            blocks_out.append(_b(f"cmp-right-{i}", "bullet", f"• {item}",
                                  col_x_right + 24, col_y + 70 + i * 64, col_w - 48, 56,
                                  size=16, weight=500, color="#ffffff", align="left", family=bfam))

    # ── kanban_columns ────────────────────────────────────────────────────
    elif slide_type == "kanban" or layout == "kanban_columns":
        heading = _get("heading", _get("title", "Framework"))
        all_items = by_type.get("kanban_item", [])

        # Group items by column index (first "||"-separated field).
        cols: dict[int, dict] = {}
        for b in all_items:
            content = b.get("content", "")
            parts = content.split("||", 2)
            if len(parts) != 3:
                continue
            try:
                col_idx = int(parts[0])
            except ValueError:
                continue
            label = parts[1].strip()
            item = parts[2].strip()
            col = cols.setdefault(col_idx, {"label": "", "items": []})
            if label and not col["label"]:
                col["label"] = label
            if item:
                col["items"].append(item)

        # Always render 3 columns; pad missing.
        ordered = [cols.get(i, {"label": f"Column {i+1}", "items": []}) for i in range(3)]

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        col_w = 360
        gap   = 30
        total_w = 3 * col_w + 2 * gap
        start_x = (W - total_w) // 2
        col_y   = 190
        col_h   = 460

        for col_idx, col in enumerate(ordered):
            cx = start_x + col_idx * (col_w + gap)
            is_accent_col = col_idx == 1  # middle column gets accent treatment
            bg = primary if is_accent_col else surface
            label_color = accent if is_accent_col else accent
            item_color  = "#ffffff" if is_accent_col else text_col
            blocks_out.append(_panel(f"kb-bg-{col_idx}", cx, col_y, col_w, col_h, bg))
            blocks_out.append(_b(f"kb-label-{col_idx}", "badge", col["label"] or f"Step {col_idx + 1}",
                                  cx + 20, col_y + 22, col_w - 40, 28,
                                  size=12, weight=800, color=label_color, align="left", family=bfam))
            for i, item in enumerate(col["items"][:4]):
                blocks_out.append(_b(f"kb-item-{col_idx}-{i}", "bullet", f"• {item}",
                                      cx + 20, col_y + 70 + i * 72, col_w - 40, 64,
                                      size=15, weight=500, color=item_color, align="left", family=bfam))

    # ── funnel_stages ─────────────────────────────────────────────────────
    elif slide_type == "funnel" or layout == "funnel_stages":
        heading = _get("heading", _get("title", "Conversion Funnel"))
        stages = by_type.get("funnel_stage", [])

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=40, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        n = min(len(stages), 5) if stages else 0
        if n == 0:
            blocks_out.append(_b("fn-empty", "text", "No funnel data",
                                  80, 220, 1120, 200,
                                  size=16, color=text_col, align="center"))
        else:
            base_y      = 200
            stage_h     = 70
            stage_gap   = 12
            widest      = 940
            narrowest   = 340
            center_x    = W // 2
            value_w     = 200
            for idx in range(n):
                stage = stages[idx]
                content = stage.get("content", "")
                label, _, value = content.partition("||")
                # Linear width taper from widest to narrowest.
                w = int(widest - (widest - narrowest) * (idx / max(1, n - 1)))
                x = center_x - w // 2
                y = base_y + idx * (stage_h + stage_gap)
                # Alternating fill for visual rhythm; deeper = darker.
                tone = idx / max(1, n - 1)
                blocks_out.append({
                    "id": f"fn-bg-{idx}", "type": "panel", "content": "",
                    "position": {"x": x, "y": y, "w": w, "h": stage_h},
                    "styling": {
                        "font_family": "", "font_size": 0, "font_weight": 0,
                        "color": "transparent",
                        "background_color": primary if tone > 0.6 else (secondary if tone > 0.3 else surface),
                        "text_align": "left",
                    },
                })
                # Stage label (left-of-center)
                blocks_out.append(_b(f"fn-label-{idx}", "text", label,
                                      x + 24, y + 18, w - value_w - 32, stage_h - 36,
                                      size=18, weight=700,
                                      color="#ffffff" if tone > 0.3 else text_col,
                                      align="left", family=hfam))
                # Stage value (right-aligned within the panel)
                blocks_out.append(_b(f"fn-value-{idx}", "text", value,
                                      x + w - value_w - 20, y + 14, value_w, stage_h - 28,
                                      size=22, weight=800,
                                      color=accent,
                                      align="right", family=hfam))

    # ── process_steps ─────────────────────────────────────────────────────
    elif layout == "process_steps":
        heading     = _get("heading", _get("title", "Process"))
        bullets_raw = _bullets()
        if not bullets_raw:
            bullets_raw = ["Step 1", "Step 2", "Step 3"]

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=44, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        n       = min(len(bullets_raw), 4)
        step_w  = min(240, (W - 120 - (n - 1) * 20) // n)
        total_w = n * step_w + (n - 1) * 20
        start_x = (W - total_w) // 2

        for idx, btext in enumerate(bullets_raw[:n]):
            cx = start_x + idx * (step_w + 20)
            blocks_out.append({
                "id": f"step-num-{idx}", "type": "process_circle",
                "content": str(idx + 1),
                "position": {"x": cx + step_w // 2 - 40, "y": 210, "w": 80, "h": 80},
                "styling": {
                    "font_family": hfam, "font_size": 32, "font_weight": 800,
                    "color": "#ffffff", "background_color": accent, "text_align": "center",
                },
            })
            if idx < n - 1:
                blocks_out.append({
                    "id": f"connector-{idx}", "type": "shape", "content": "",
                    "position": {"x": cx + step_w + 25, "y": 248, "w": 15, "h": 4},
                    "styling": {
                        "font_family": "", "font_size": 0, "font_weight": 0,
                        "color": accent, "background_color": accent, "text_align": "left",
                    },
                })
            blocks_out.append(_b(f"step-{idx}", "text", btext,
                                  cx, 310, step_w, 180,
                                  size=15, weight=600, color=text_col, align="center", family=hfam))

    # ── closing ────────────────────────────────────────────────────────────
    elif slide_type == "closing" or layout == "closing":
        cta = _get("title",    "Let's Get Started")
        sub = _get("subtitle", _get("body", "Contact us to learn more"))

        # Tune the layout by subtitle length. Short CTAs ("Contact us") keep
        # the dramatic short-CTA look; long paragraphs get a smaller font,
        # taller box, and the title is shrunk and lifted to make room.
        sub_len = len(sub.strip()) if sub else 0
        long_sub = sub_len > 90

        if long_sub:
            title_y, title_h, title_size = 150, 140, 48
            sub_y,   sub_h,   sub_size   = 320, 320, 20
            bar_y = 130
        else:
            title_y, title_h, title_size = 215, 170, 64
            sub_y,   sub_h,   sub_size   = 410, 70,  22
            bar_y = 190

        blocks_out.append(_accent_bar(480, bar_y, 320, 5))
        blocks_out.append(_b("cta", "title", cta,
                              80, title_y, 1120, title_h,
                              size=title_size, weight=800, color="#ffffff",
                              align="center", family=hfam))
        if sub:
            blocks_out.append(_b("sub", "subtitle", sub,
                                  80, sub_y, 1120, sub_h,
                                  size=sub_size, weight=400,
                                  color="rgba(255,255,255,0.85)", align="center"))

    # ── content_clean (fallback) ───────────────────────────────────────────
    else:
        heading     = _get("heading", _get("title", "Section Title"))
        body_text   = _get("body", "")
        bullets_raw = _bullets()

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 80,
                              size=44, weight=800, color=primary,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        bullet_text = "\n".join(f"• {b}" for b in bullets_raw if b)
        if not bullet_text:
            bullet_text = body_text
        blocks_out.append(_b("body", "bullet", bullet_text,
                              60, 182, 1160, 490,
                              size=24, weight=400, color=text_col))

    return [b for b in blocks_out if b]


def _slide_background(slide_type: str, slide_layout: str, _kw: str, theme_colors: dict) -> dict:
    """Pick a backdrop that gives the slide visual weight.

    Light/neutral slides get a subtle two-stop gradient (background → surface)
    so they don't feel like a blank cream page. Dark/accent slides get a
    richer multi-stop gradient with a hint of the accent color for depth.
    """
    primary   = theme_colors.get("primary",    "#0F172A")
    secondary = theme_colors.get("secondary",  "#1E293B")
    accent    = theme_colors.get("accent",     "#6366F1")
    bg        = theme_colors.get("background", "#FFFFFF")
    surface   = theme_colors.get("surface",    "#F1F5F9")

    if slide_type in ("title", "closing") or slide_layout in ("title_hero", "closing"):
        # Dramatic hero — deep gradient with accent glow.
        return {
            "type": "gradient",
            "value": f"linear-gradient(135deg, {primary} 0%, {secondary} 55%, {accent}40 100%)",
        }
    if slide_type == "quote" or slide_layout == "quote_centered":
        return {
            "type": "gradient",
            "value": f"linear-gradient(160deg, {primary} 0%, {secondary} 70%, {accent}25 100%)",
        }
    if slide_type == "stats" or slide_layout == "stats_showcase":
        return {
            "type": "gradient",
            "value": f"linear-gradient(135deg, {primary} 0%, {secondary} 60%, {accent}30 100%)",
        }
    # All other slides get a soft two-stop gradient — much warmer than flat bg.
    # Tiny opacity hint of accent for visual interest without hurting legibility.
    return {
        "type": "gradient",
        "value": f"linear-gradient(165deg, {bg} 0%, {surface} 70%, {accent}08 100%)",
    }


def _system_layout(slide_type: str, content: dict, slide_index: int = 0) -> str:
    # CRITICAL: when slide_type is explicit, it WINS over content-shape auto-
    # detection. Gemini fills every field in the schema (even unused ones), so
    # a kanban slide can carry a `comparison` object too — without this guard
    # the auto-detector picks comparison_split and the panels render wrong.
    EXPLICIT_LAYOUTS = {
        "title":      "title_hero",
        "closing":    "closing",
        "agenda":     "agenda_rows",
        "chart":      "chart_showcase",
        "roadmap":    "roadmap_timeline",
        "comparison": "comparison_split",
        "kanban":     "kanban_columns",
        "funnel":     "funnel_stages",
        "stats":      "stats_showcase",
        "quote":      "quote_centered",
    }
    if slide_type in EXPLICIT_LAYOUTS:
        return EXPLICIT_LAYOUTS[slide_type]

    # Fallback inference for slides without an explicit type — pick the layout
    # whose supporting data the LLM actually filled.
    if isinstance(content.get("chart"), dict) and content["chart"].get("data"):
        return "chart_showcase"
    if isinstance(content.get("roadmap"), list) and content["roadmap"]:
        return "roadmap_timeline"
    if isinstance(content.get("comparison"), dict) and (
        (content["comparison"].get("left") or {}).get("items")
        or (content["comparison"].get("right") or {}).get("items")
    ):
        return "comparison_split"
    if isinstance(content.get("columns"), list) and any(
        isinstance(c, dict) and (c.get("items") or []) for c in content["columns"]
    ):
        return "kanban_columns"
    if isinstance(content.get("funnel"), list) and content["funnel"]:
        return "funnel_stages"
    if content.get("stats"): return "stats_showcase"
    if content.get("quote"): return "quote_centered"

    bullets = content.get("bullets", [])
    heading = content.get("heading", "").lower()
    body    = content.get("body",    "")

    process_words = ("process", "step", "phase", "how", "workflow", "pipeline", "framework", "journey")
    if any(w in heading for w in process_words) and 2 <= len(bullets) <= 5:
        return "process_steps"

    if 2 <= len(bullets) <= 5:
        return "split_panel" if slide_index % 2 == 0 else "card_grid"

    return "content_clean"


def _trim_bullet(text: str, max_words: int) -> str:
    """Smart bullet density: keep bullets scannable.

    - Strip leading list markers ("- ", "• ", "1. ").
    - If under max_words, return as-is.
    - Else: truncate to max_words and snap to the last clause boundary
      (',', ';', '—') if one exists in the kept range, so the bullet ends
      cleanly instead of mid-thought.
    """
    if not isinstance(text, str):
        return ""
    s = text.strip()
    # Drop common list markers the LLM sometimes leaves in.
    for marker in ("- ", "• ", "* ", "– "):
        if s.startswith(marker):
            s = s[len(marker):].strip()
            break
    # Drop leading "N. " or "N) " numbering.
    if len(s) >= 3 and s[0].isdigit():
        cut = 1
        while cut < len(s) and s[cut].isdigit():
            cut += 1
        if cut < len(s) and s[cut] in ".)" and cut + 1 < len(s) and s[cut + 1] == " ":
            s = s[cut + 2:].strip()

    words = s.split()
    if len(words) <= max_words:
        return s.rstrip(",;: ")

    kept = " ".join(words[:max_words])
    # Snap to last clause boundary in the kept range to end cleanly.
    for sep in (";", ",", " — ", " – "):
        idx = kept.rfind(sep)
        if idx > len(kept) // 2:
            return kept[:idx].rstrip(",;: ")
    return kept.rstrip(",;: ") + "…"


# Bullet density caps: keep slides scannable, not paragraphs.
# 16 words ≈ one full speaker-paced line at our default body font, which is
# what Gamma-quality decks tend to hit. Anything longer wraps awkwardly.
_BULLET_MAX_WORDS = 16
_BULLET_MAX_COUNT = 6


def _has_structured_data(slide_type: str, content: dict) -> bool:
    """Check if a structured slide type has enough real data to render.

    Gemini sometimes picks a rich type ("comparison", "chart", etc.) but
    leaves the supporting object empty. Detecting that lets us downgrade
    the slide to plain content instead of rendering empty panels.
    """
    if not isinstance(content, dict):
        return False
    if slide_type == "chart":
        chart = content.get("chart") or {}
        data = chart.get("data") if isinstance(chart, dict) else None
        return bool(data) and any(
            isinstance(d, dict) and str(d.get("label", "")).strip() for d in data
        )
    if slide_type == "roadmap":
        rm = content.get("roadmap") or []
        return any(
            isinstance(s, dict) and (str(s.get("phase", "")).strip() or str(s.get("label", "")).strip())
            for s in rm
        )
    if slide_type == "comparison":
        cmp = content.get("comparison") or {}
        if not isinstance(cmp, dict):
            return False
        left  = (cmp.get("left")  or {}).get("items") or []
        right = (cmp.get("right") or {}).get("items") or []
        return any(str(x).strip() for x in left) and any(str(x).strip() for x in right)
    if slide_type == "kanban":
        cols = content.get("columns") or []
        if not isinstance(cols, list) or len(cols) < 2:
            return False
        # At least 2 of the 3 columns must have at least 1 item.
        filled = sum(
            1 for c in cols
            if isinstance(c, dict) and any(str(x).strip() for x in (c.get("items") or []))
        )
        return filled >= 2
    if slide_type == "funnel":
        stages = content.get("funnel") or []
        return sum(
            1 for s in stages
            if isinstance(s, dict) and (str(s.get("label", "")).strip() or str(s.get("value", "")).strip())
        ) >= 2
    if slide_type == "stats":
        return bool(content.get("stats"))
    if slide_type == "quote":
        return bool(str(content.get("quote") or "").strip())
    return True  # title/agenda/content/closing always pass


def _content_to_blocks(content: dict, slide_type: str) -> list[dict]:
    blocks: list[dict] = []
    heading = content.get("heading", "")
    body    = content.get("body",    "")
    raw_bullets = content.get("bullets", []) or []
    bullets = [b for b in (_trim_bullet(x, _BULLET_MAX_WORDS) for x in raw_bullets) if b][:_BULLET_MAX_COUNT]
    stats   = content.get("stats",   [])
    quote   = content.get("quote",   "")
    caption = content.get("caption", "")

    if slide_type in ("title", "closing"):
        if heading:
            blocks.append({"id": "title-0", "type": "title",    "content": heading})
        sub = bullets[0] if bullets else body
        if sub:
            blocks.append({"id": "subtitle-0", "type": "subtitle", "content": sub})

    elif slide_type == "quote":
        if quote:
            blocks.append({"id": "quote-0",   "type": "quote",   "content": quote})
        if caption:
            blocks.append({"id": "caption-0", "type": "caption", "content": caption})

    elif slide_type == "stats":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        for idx, s in enumerate(stats):
            parts     = s.split(maxsplit=1)
            formatted = f"{parts[0]}\n{parts[1]}" if len(parts) > 1 else s
            blocks.append({"id": f"stat-{idx}", "type": "stat", "content": formatted})

    elif slide_type == "chart":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        chart = content.get("chart") or {}
        ctype = (chart.get("type") or "bar").lower()
        if ctype not in ("bar", "line", "pie"):
            ctype = "bar"
        raw_data = chart.get("data") or []
        clean: list[dict] = []
        for d in raw_data:
            if not isinstance(d, dict):
                continue
            label = str(d.get("label", "")).strip()
            try:
                value = float(d.get("value", 0))
            except (TypeError, ValueError):
                continue
            if label:
                clean.append({"label": label, "value": value})
        if clean:
            blocks.append({
                "id": "chart-0",
                "type": "chart",
                "content": "",
                "chart_type": ctype,
                "chart_data": clean,
            })
        if caption:
            blocks.append({"id": "caption-0", "type": "caption", "content": caption})

    elif slide_type == "roadmap":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        for idx, step in enumerate(content.get("roadmap") or []):
            if not isinstance(step, dict):
                continue
            phase = str(step.get("phase", f"Phase {idx + 1}")).strip()
            label = str(step.get("label", "")).strip()
            blocks.append({
                "id": f"roadmap-{idx}",
                "type": "roadmap_step",
                "content": f"{phase}||{label}",
            })

    elif slide_type == "comparison":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        cmp = content.get("comparison") or {}
        for side_key in ("left", "right"):
            side = cmp.get(side_key) or {}
            if not isinstance(side, dict):
                continue
            label = str(side.get("label", side_key.title())).strip()
            items = side.get("items") or []
            for idx, item in enumerate(items[:5]):
                text = _trim_bullet(str(item), _BULLET_MAX_WORDS)
                if not text:
                    continue
                blocks.append({
                    "id": f"cmp-{side_key}-{idx}",
                    "type": f"comparison_{side_key}",
                    "content": f"{label}||{text}" if idx == 0 else f"||{text}",
                })

    elif slide_type == "kanban":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        cols = content.get("columns") or []
        for col_idx, col in enumerate(cols[:3]):
            if not isinstance(col, dict):
                continue
            label = str(col.get("label", f"Column {col_idx + 1}")).strip()
            items = col.get("items") or []
            for idx, item in enumerate(items[:4]):
                text = _trim_bullet(str(item), _BULLET_MAX_WORDS)
                if not text:
                    continue
                blocks.append({
                    "id": f"kb-{col_idx}-{idx}",
                    "type": "kanban_item",
                    "content": f"{col_idx}||{label}||{text}" if idx == 0 else f"{col_idx}||||{text}",
                })

    elif slide_type == "funnel":
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        for idx, stage in enumerate(content.get("funnel") or []):
            if not isinstance(stage, dict):
                continue
            label = str(stage.get("label", f"Stage {idx + 1}")).strip()
            value = str(stage.get("value", "")).strip()
            blocks.append({
                "id": f"fn-{idx}",
                "type": "funnel_stage",
                "content": f"{label}||{value}",
            })

    else:
        if heading:
            blocks.append({"id": "heading-0", "type": "heading", "content": heading})
        if body:
            blocks.append({"id": "body-0",    "type": "body",    "content": body})
        for idx, b in enumerate(bullets):
            blocks.append({"id": f"bullet-{idx}", "type": "bullet", "content": b})

    return blocks


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class SlideGeneratorAgent:
    async def run(
        self,
        outline: list[dict],
        analysis: dict[str, Any],
        mapping: TemplateMappingResult,
        logo_url: str = "",
        max_concurrency: int = 3,
    ) -> list[dict]:
        analysis_summary = json.dumps(analysis, indent=2)
        outline_summary = json.dumps(
            [
                {
                    "order": item.get("order"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "key_points": item.get("key_points", []),
                }
                for item in outline
            ],
            indent=2,
        )
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _throttled(item: dict) -> dict:
            async with semaphore:
                return await self._generate_slide_content(item, analysis_summary, outline_summary)

        logger.info(f"Generating {len(outline)} slides (max {max_concurrency} concurrent)")
        contents = await asyncio.gather(*[_throttled(item) for item in outline])
        return self._build_slides(outline, list(contents), mapping, logo_url)

    async def _generate_slide_content(
        self, outline_item: dict, analysis_summary: str, full_outline: str
    ) -> dict:
        prompt = render(
            SLIDE_CONTENT_PROMPT,
            outline_item=json.dumps(outline_item, indent=2),
            full_outline=full_outline,
            analysis_summary=analysis_summary,
            slide_type=outline_item.get("type", "content"),
        )
        return await gemini_client.generate_json(prompt)

    def _build_slides(
        self,
        outline: list[dict],
        contents: list[dict],
        mapping: TemplateMappingResult,
        logo_url: str,
    ) -> list[dict]:
        theme_colors = mapping.theme.colors
        theme_fonts  = mapping.theme.fonts
        result: list[dict] = []
        content_idx = 0

        RICH_TYPES = {"chart", "roadmap", "quote", "stats", "comparison", "kanban", "funnel"}
        for i, (outline_item, content) in enumerate(zip(outline, contents)):
            outline_type = outline_item.get("type", "content")
            llm_type = (content.get("type") or "").strip().lower() if isinstance(content, dict) else ""
            if outline_type in ("title", "agenda"):
                slide_type = outline_type
            elif outline_type == "closing":
                slide_type = llm_type if llm_type in RICH_TYPES else "closing"
            elif llm_type in RICH_TYPES:
                slide_type = llm_type
            else:
                slide_type = outline_type
            # Downgrade structured types that lack supporting data.
            if slide_type in RICH_TYPES and isinstance(content, dict) and not _has_structured_data(slide_type, content):
                slide_type = "content"
                if not content.get("bullets") and not content.get("body"):
                    h = content.get("heading") or outline_item.get("title", "")
                    if h:
                        content["bullets"] = [h]
            slide_layout = _system_layout(slide_type, content, slide_index=content_idx)
            if slide_type not in ("title", "closing"):
                content_idx += 1

            gen_blocks = _content_to_blocks(content, slide_type)
            blocks     = _layout_blocks(slide_type, slide_layout, gen_blocks, theme_colors, theme_fonts)
            background = _slide_background(slide_type, slide_layout, "", theme_colors)

            result.append({
                "order":      outline_item.get("order", i + 1),
                "type":       slide_type,
                "background": background,
                "blocks":     blocks,
            })

        return result
