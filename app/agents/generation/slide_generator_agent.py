from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.ai import gemini_client
from app.ai.prompt_templates import SLIDE_CONTENT_PROMPT, render
from app.agents.generation.template_mapper_agent import TemplateMappingResult
from app.config import settings
from app.services import backdrop_service
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Editor backdrops are DISABLED for all slide types. Reason:
#
# 1. Gamma's premium look doesn't actually use full-bleed photographic
#    backdrops on most slides — it relies on disciplined typography,
#    contained cards on theme bg, and AI illustrations only as content
#    blocks (right-half of title/closing).
# 2. Heavy backdrop overlays conflict with the existing layout color logic,
#    which uses theme.primary for headings (a dark color on light themes).
#    Dark heading + dark overlay = invisible text.
# 3. Title/closing slides use a split-card composition with the AI image
#    on the right, not a backdrop. See `title_hero` layout below.
#
# To re-enable per-variant backdrops later, repopulate this dict and toggle
# EDITOR_BACKDROPS_ENABLED in settings. Logic to attach them survives in
# slide_generator_agent + generate_stream.
_VARIANT_FOR_TYPE: dict[str, str] = {}


def _backdrop_variant(slide_type: str) -> str | None:
    return _VARIANT_FOR_TYPE.get(slide_type)

W, H = 1280, 720


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_luminance(hex_color: str) -> float:
    """Perceived luminance 0.0 (black) → 1.0 (white) for a hex color.
    Used to detect dark themes so we can swap heading/card colors."""
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return 1.0
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 1.0
    # Rec. 709 perceived luminance.
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _is_dark_hex(hex_color: str) -> bool:
    return _hex_luminance(hex_color) < 0.35


def _is_light_hex(hex_color: str) -> bool:
    return _hex_luminance(hex_color) >= 0.65


def _premium_card_pair(theme_colors: dict) -> tuple[str, str, str, str]:
    """Return (dark_bg, dark_text, light_bg, light_text) for alternating cards."""
    primary = theme_colors.get("primary", "#0F172A")
    surface = theme_colors.get("surface", "#F1F5F9")
    text_col = theme_colors.get("text", "#0F172A")
    return primary, "#ffffff", surface, text_col


def _is_card_dark(idx: int) -> bool:
    """Gamma-style: top-left and bottom-right are dark, others light."""
    return idx in (0, 3)


# Keyword → Lucide-React icon name. Used to auto-pick a card icon from its
# content. Order matters — first matching keyword wins. Covers the most
# common deck topics; falls back to a generic accent icon.
_ICON_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("revenue", "money", "cost", "price", "budget", "roi", "saving"), "dollar-sign"),
    (("data", "analytic", "metric", "chart", "graph", "report"), "bar-chart-3"),
    (("ai", "intelligence", "ml", "machine", "model", "neural"), "sparkles"),
    (("automate", "automation", "workflow", "process", "pipeline"), "zap"),
    (("team", "people", "collaborat", "user", "customer", "audience"), "users"),
    (("growth", "scale", "expand", "increase", "performance"), "trending-up"),
    (("design", "brand", "creative", "visual", "aesthetic"), "palette"),
    (("security", "secure", "trust", "safety", "privacy", "compliance"), "shield-check"),
    (("speed", "fast", "quick", "instant", "real-time", "rapid"), "rocket"),
    (("quality", "premium", "best", "excellence"), "award"),
    (("integration", "connect", "api", "sync", "import", "export"), "plug-zap"),
    (("idea", "innovation", "concept", "research", "discover"), "lightbulb"),
    (("global", "international", "world", "country", "region"), "globe"),
    (("target", "goal", "objective", "milestone", "kpi"), "target"),
    (("strategy", "plan", "roadmap", "vision"), "compass"),
    (("communication", "message", "chat", "notification"), "message-circle"),
    (("storage", "database", "file", "document"), "database"),
    (("mobile", "device", "platform"), "smartphone"),
    (("schedule", "time", "calendar", "deadline"), "calendar"),
    (("alert", "warning", "risk", "issue"), "alert-triangle"),
    (("success", "win", "achievement", "complete", "deliver"), "check-circle-2"),
    (("learn", "training", "education", "course"), "graduation-cap"),
    (("market", "sales", "go-to-market", "gtm"), "shopping-cart"),
    (("code", "developer", "engineer", "build", "tech"), "code-2"),
    (("cloud", "saas", "service", "hosting"), "cloud"),
]


def _pick_card_icon(content: str) -> str:
    """Pick a Lucide icon name based on card text content. Falls back to
    a generic 'circle-check' icon when no keyword matches."""
    text = (content or "").lower()
    for keywords, icon in _ICON_KEYWORDS:
        if any(kw in text for kw in keywords):
            return icon
    return "circle-check"


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

    # Per-theme design tokens (stored under fonts._tokens). Each Gamma-tier
    # theme can override heading size, card radius, illustration mood, etc.
    # Falls back to safe defaults for themes that don't declare tokens.
    tokens = theme_fonts.get("_tokens", {}) if isinstance(theme_fonts, dict) else {}
    THEME_HEADING_SIZE = int(tokens.get("heading_size", 56))

    # Dark-mode detection. Page-bg luminance < 0.35 → dark theme.
    is_dark = _is_dark_hex(bg_col)

    # In dark mode, `primary` is typically reused as a heading-text color
    # downstream (which would render dark-on-dark and be invisible). Solution:
    # keep `primary` as-is for panel/card backgrounds (which legitimately use
    # a dark color), but introduce a separate `heading_color` for text. All
    # heading text below now uses `heading_color` instead of `primary`.
    if is_dark:
        heading_color = "#FAFAFA"
        text_col = text_col if _is_light_hex(text_col) else "#E5E5E5"
    else:
        heading_color = primary

    hfam = theme_fonts.get("heading", {}).get("family", "Inter, sans-serif")
    bfam = theme_fonts.get("body",    {}).get("family", "Inter, sans-serif")

    if is_dark:
        # Gamma-style translucent cards: subtle white-on-dark wash that lets
        # the underlying deck backdrop (wavy texture) show through, instead
        # of opaque theme-primary rectangles that clash with the backdrop.
        # Two slightly different alphas keep visual rhythm between cards.
        dark_card_bg, dark_card_text = "rgba(255,255,255,0.07)", "#FAFAFA"
        light_card_bg, light_card_text = "rgba(255,255,255,0.04)", "#E5E5E5"
    else:
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

    # ── title_hero (Gamma "Industry Benchmark"-style: full-bleed dark photo
    # background, title centered across the canvas) ──
    if slide_type == "title" or layout == "title_hero":
        title_text = _get("title", "Presentation Title")
        sub_text   = _get("subtitle", "")

        # Text spans the full slide width and centers — sits on top of a
        # full-bleed background photo (added later by the preview/stream
        # pipeline). Generous left/right margins keep the title from
        # touching the edges.
        TEXT_X = 120
        TEXT_W = W - 240  # 1040 at W=1280

        title_len = len(title_text)
        if   title_len <= 18:  t_size, t_height, t_y = 96, 200, 240
        elif title_len <= 30:  t_size, t_height, t_y = 80, 240, 220
        elif title_len <= 50:  t_size, t_height, t_y = 64, 280, 200
        elif title_len <= 80:  t_size, t_height, t_y = 48, 320, 180
        else:                  t_size, t_height, t_y = 38, 360, 160

        bar_y = t_y + t_height + 16
        sub_y = bar_y + 26

        # Title text colors adapt to theme lightness — dark themes get white
        # text (will sit on a dark atmospheric backdrop), light themes get
        # the theme's heading color (will sit on a light/cream backdrop).
        title_text_color = "#ffffff" if is_dark else heading_color
        eyebrow_color = "rgba(255,255,255,0.85)" if is_dark else heading_color
        subtitle_color = (
            "rgba(255,255,255,0.85)" if is_dark
            else f"rgba(0,0,0,0.7)"
        )
        foot_color = (
            "rgba(255,255,255,0.55)" if is_dark else "rgba(0,0,0,0.5)"
        )

        blocks_out.append(_b("hero-eyebrow", "badge", "PRESENTATION",
                              TEXT_X, 80, 200, 28,
                              size=11, weight=800, color=eyebrow_color,
                              align="center", family=bfam))

        blocks_out.append(_b("title", "title", title_text,
                              TEXT_X, t_y, TEXT_W, t_height,
                              size=t_size, weight=900, color=title_text_color,
                              align="center", family=hfam))
        # Centered accent bar — width-aware so it visually grounds the title.
        bar_w = 120
        blocks_out.append(_accent_bar(TEXT_X + (TEXT_W - bar_w) // 2, bar_y, bar_w, 4))
        if sub_text:
            blocks_out.append(_b("subtitle", "subtitle", sub_text,
                                  TEXT_X, sub_y, TEXT_W, 90,
                                  size=22, weight=400,
                                  color=subtitle_color, align="center"))

        # Bottom-center brand mark — subtle but signals polish.
        blocks_out.append(_b("hero-foot", "caption", "Crafted with WAC Deck Studio",
                              TEXT_X, H - 50, TEXT_W, 22,
                              size=11, weight=600,
                              color=foot_color, align="center", family=bfam))

    # ── agenda_rows (Gamma editorial: big numerals on the left, refined
    # serif-weight text on the right, thin divider between items — no heavy
    # rectangles) ─
    elif slide_type == "agenda" or layout == "agenda_rows":
        heading = _get("heading", _get("title", "Agenda"))
        bullets  = _bullets()
        body_text = _get("body", "")

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)

        # Width-aware heading sizing — long titles wrap to 2 lines and need
        # taller heading blocks so they don't overflow into the items below.
        # The items_start_y also shifts so divider/numerals don't overlap.
        head_len = len(heading)
        if head_len <= 30:
            h_size, h_height = 56, 80
        elif head_len <= 50:
            h_size, h_height = 46, 130
        else:
            h_size, h_height = 38, 160
        head_y = 78
        bar_y = head_y + h_height + 10
        items_start_y = bar_y + 30

        blocks_out.append(_b("heading", "heading", heading,
                              60, head_y, W - 120, h_height,
                              size=h_size, weight=800, color=heading_color,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, bar_y, 100, 4))

        # Fallback — if Gemini routed this slide to "agenda" type but didn't
        # produce list items, derive them from the body text by splitting on
        # sentence/clause boundaries. Avoids the empty-slide failure mode.
        if not bullets and body_text:
            import re as _re
            parts = [
                p.strip() for p in _re.split(r"(?<=[.!?])\s+|\n+|;\s+", body_text)
                if len(p.strip()) > 4
            ]
            if len(parts) >= 2:
                bullets = parts[:6]
            else:
                # Single block of body — render it as a paragraph under the
                # heading so the slide isn't empty.
                blocks_out.append(_b("agenda-body", "body", body_text,
                                      60, items_start_y, W - 120, 360,
                                      size=22, weight=400,
                                      color=text_col, align="left", family=bfam))
                return blocks_out

        n_rows = min(len(bullets), 6)
        gap = 8
        avail_h = H - items_start_y - 60  # leave 60px breathing room at bottom
        start_y = items_start_y
        row_h = max(64, min(98, (avail_h - (n_rows - 1) * gap) // max(n_rows, 1)))
        item_text_color = "#FAFAFA" if is_dark else "#0A0A0A"
        num_color = "rgba(255,255,255,0.35)" if is_dark else "rgba(0,0,0,0.30)"
        divider_color = "rgba(255,255,255,0.10)" if is_dark else "rgba(0,0,0,0.10)"

        for idx, b in enumerate(bullets[:6]):
            clean = b.lstrip("•-* ").strip()
            cy = start_y + idx * (row_h + gap)
            num_w = 90
            text_x = 60 + num_w + 24

            # Large muted index numeral on the left — Gamma editorial signature.
            blocks_out.append(_b(f"agenda-num-{idx}", "text", f"{idx + 1:02d}",
                                  60, cy + (row_h - 60) // 2, num_w, 60,
                                  size=44, weight=300, color=num_color,
                                  align="left", family=hfam))
            # Item text — strong, refined.
            blocks_out.append(_b(f"agenda-row-{idx}", "text", clean,
                                  text_x, cy + (row_h - 32) // 2,
                                  W - text_x - 60, 40,
                                  size=22, weight=600, color=item_text_color,
                                  align="left", family=hfam))
            # Thin divider after each row except the last.
            if idx < n_rows - 1:
                blocks_out.append({
                    "id": f"agenda-div-{idx}", "type": "shape", "content": "",
                    "position": {"x": 60, "y": cy + row_h + gap // 2 - 1,
                                 "w": W - 120, "h": 1},
                    "styling": {
                        "font_family": "", "font_size": 0, "font_weight": 0,
                        "color": divider_color, "background_color": divider_color,
                        "text_align": "left",
                    },
                })

    # ── split_panel (full-width 2x2 cards — no left panel, no illustration;
    # the deck-wide atmospheric backdrop fills the slide visually) ─────────
    elif layout == "split_panel":
        heading     = _get("heading", _get("title", "Section"))
        bullets_raw = _bullets()

        bdg = _badge(badge_label, 60, 35)
        if bdg:
            blocks_out.append(bdg)

        blocks_out.append(_b("heading", "heading", heading,
                              60, 75, W - 120, 100,
                              size=52, weight=800, color=heading_color,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 180, 100, 4))

        # 2x2 card grid spanning full slide width.
        grid_x = 60
        grid_w = W - 120                # 1160
        card_w = (grid_w - 28) // 2     # 566 per card with 28px gap
        card_h = 220                    # taller cards since we have more space
        grid_y = 220
        row_gap = 24

        for idx, btext in enumerate(bullets_raw[:4]):
            parts = btext.split(": ", 1) if ": " in btext else [btext, ""]
            title_line = parts[0]
            body_line  = parts[1] if len(parts) > 1 else ""
            content_str = f"{title_line}\n{body_line}" if body_line else title_line

            col = idx % 2
            row = idx // 2
            cx  = grid_x + col * (card_w + 28)
            cy  = grid_y + row * (card_h + row_gap)

            is_dark   = _is_card_dark(idx)
            card_bg   = dark_card_bg   if is_dark else light_card_bg
            card_text = dark_card_text if is_dark else light_card_text

            blocks_out.append({
                "id": f"card-{idx}", "type": "card", "content": content_str,
                "icon": _pick_card_icon(content_str),
                "position": {"x": cx, "y": cy, "w": card_w, "h": card_h},
                "styling": {
                    "font_family": hfam, "font_size": 18, "font_weight": 700,
                    "color": card_text, "background_color": card_bg, "text_align": "left",
                },
            })

        if not bullets_raw:
            body_text = _get("body", "")
            blocks_out.append(_b("body", "bullet", body_text,
                                  60, 220, W - 120, 460,
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
                              size=56, weight=800, color=heading_color,
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
                "icon": _pick_card_icon(btext),  # Gamma-style icon per card
                "position": {"x": cx, "y": cy, "w": card_w, "h": card_h},
                "styling": {
                    "font_family": hfam, "font_size": 20, "font_weight": 700,
                    "color": card_text, "background_color": card_bg, "text_align": "left",
                },
            })

    # ── stats (Gamma "One year. Five numbers." — HUGE numbers, no cards) ──
    elif slide_type == "stats" or layout == "stats_showcase":
        heading = _get("heading", _get("title", "Key Metrics"))
        stats   = by_type.get("stat", [])
        if not stats:
            stats = [{"id": f"s{i}", "content": f"Metric {i+1}"} for i in range(3)]

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        # Smaller heading because the NUMBERS are the hero, not the title.
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 60,
                              size=36, weight=700, color="#ffffff",
                              align="center", family=hfam))

        n = min(len(stats), 5)
        # Layout: 3 stats in a single row of 3; 4 in 2x2; 5 in 3-over-2.
        # Numbers are HUGE (~110pt) with thin labels underneath — matches
        # Gamma's "<5min  45%  4+" look directly.
        STAT_NUM_SIZE = 110 if n <= 3 else 90
        LABEL_SIZE    = 16
        DESC_SIZE     = 13

        def _stat_block(stat: dict, idx: int, sx: int, sy: int, sw: int, sh: int) -> None:
            content = stat.get("content", "—")
            # Content is "BIG\nLABEL\nDescription...". Split into the 3 parts.
            parts = content.split("\n", 2) if "\n" in content else [content]
            big   = parts[0].strip() if len(parts) > 0 else "—"
            label = parts[1].strip() if len(parts) > 1 else ""
            desc  = parts[2].strip() if len(parts) > 2 else ""

            # HUGE number — accent-colored, ultra-bold, no card background.
            blocks_out.append(_b(f"stat-num-{idx}", "text", big,
                                  sx, sy, sw, STAT_NUM_SIZE + 20,
                                  size=STAT_NUM_SIZE, weight=900,
                                  color="#ffffff", align="center", family=hfam))
            # Label — tighter spacing under the number.
            if label:
                blocks_out.append(_b(f"stat-label-{idx}", "text", label,
                                      sx, sy + STAT_NUM_SIZE + 10, sw, 30,
                                      size=LABEL_SIZE, weight=700,
                                      color="#ffffff", align="center", family=hfam))
            # Optional description — small, dimmed.
            if desc:
                blocks_out.append(_b(f"stat-desc-{idx}", "text", desc,
                                      sx + 10, sy + STAT_NUM_SIZE + 48, sw - 20, 80,
                                      size=DESC_SIZE, weight=400,
                                      color="rgba(255,255,255,0.65)", align="center", family=bfam))

        if n <= 3:
            # Single row, equally spaced.
            stat_w = (W - 160) // n
            base_y = 200
            for idx, stat in enumerate(stats[:n]):
                sx = 80 + idx * stat_w
                _stat_block(stat, idx, sx, base_y, stat_w, 400)
        elif n == 4:
            # 2x2 grid.
            stat_w = (W - 200) // 2
            for idx, stat in enumerate(stats[:4]):
                col = idx % 2
                row = idx // 2
                sx = 80 + col * (stat_w + 40)
                sy = 175 + row * 240
                _stat_block(stat, idx, sx, sy, stat_w, 220)
        else:
            # 5 stats: 3 on top, 2 centered on bottom.
            top_w = (W - 200) // 3
            for idx in range(3):
                sx = 80 + idx * (top_w + 20)
                _stat_block(stats[idx], idx, sx, 175, top_w, 220)
            bot_w = (W - 240) // 2
            bot_offset = (W - 2 * bot_w - 40) // 2
            for j in range(2):
                idx = 3 + j
                if idx >= len(stats):
                    break
                sx = bot_offset + j * (bot_w + 40)
                _stat_block(stats[idx], idx, sx, 425, bot_w, 220)

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
                              size=52, weight=800, color=heading_color,
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

    # ── roadmap (Gamma chevron-arrows in 2x2 or 1x4 grid) ─────────────────
    elif slide_type == "roadmap" or layout == "roadmap_timeline":
        # Gamma "From idea to finished deck in four steps" layout —
        # stacked horizontal arrow bars (Input → Generate → Design → Ship).
        # Each bar shows: step label inside the arrow + optional description
        # beneath. Cascading indent gives the visual flow.
        heading = _get("heading", _get("title", "Roadmap"))
        steps = by_type.get("roadmap_step", [])

        bdg = _badge(badge_label)
        if bdg:
            blocks_out.append(bdg)
        blocks_out.append(_b("heading", "heading", heading,
                              60, 68, 1160, 70,
                              size=52, weight=800, color=heading_color,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 148, 100, 4))

        n = min(len(steps), 6) if steps else 0
        if n == 0:
            blocks_out.append(_b("roadmap-empty", "text", "No roadmap data",
                                  80, 220, 1120, 200,
                                  size=16, color=text_col, align="center"))
        else:
            # Editorial timeline: prominent phase label on the LEFT (accent
            # color, bold), description on the RIGHT, hairline divider between
            # rows. Matches the new agenda style — clean, premium, no heavy
            # rectangles. A subtle vertical rail on the far left connects
            # the timeline visually.
            margin_x = 80
            start_y = 200
            avail_h = H - start_y - 60
            row_h = max(70, min(110, (avail_h - (n - 1) * 8) // n))
            gap = 8
            rail_x = margin_x
            phase_x = margin_x + 30
            phase_w = 220
            desc_x = phase_x + phase_w + 24
            desc_w = W - desc_x - margin_x
            divider_col = "rgba(255,255,255,0.10)" if is_dark else "rgba(0,0,0,0.10)"
            label_col = "#FAFAFA" if is_dark else "#0A0A0A"

            # Vertical rail spanning all rows.
            rail_h = n * (row_h + gap) - gap
            blocks_out.append({
                "id": "rm-rail", "type": "shape", "content": "",
                "position": {"x": rail_x, "y": start_y + 8, "w": 2, "h": rail_h - 16},
                "styling": {
                    "font_family": "", "font_size": 0, "font_weight": 0,
                    "color": accent, "background_color": accent + "55", "text_align": "left",
                },
            })

            for idx, step in enumerate(steps[:n]):
                raw = step.get("content", "")
                phase, _, label = raw.partition("||")
                by = start_y + idx * (row_h + gap)

                # Dot on the rail for this row.
                blocks_out.append({
                    "id": f"rm-dot-{idx}", "type": "shape", "content": "",
                    "position": {"x": rail_x - 5, "y": by + row_h // 2 - 6, "w": 12, "h": 12},
                    "styling": {
                        "font_family": "", "font_size": 0, "font_weight": 0,
                        "color": accent, "background_color": accent, "text_align": "left",
                    },
                })
                # Phase label (Q3 2024, etc) — accent color, bold.
                phase_text = phase.strip() or f"Phase {idx + 1}"
                blocks_out.append(_b(f"rm-phase-{idx}", "text", phase_text,
                                      phase_x, by + (row_h - 32) // 2,
                                      phase_w, 36,
                                      size=20, weight=800, color=accent,
                                      align="left", family=hfam))
                # Description on the right.
                desc_text = label.strip() or phase_text
                blocks_out.append(_b(f"rm-label-{idx}", "text", desc_text,
                                      desc_x, by + (row_h - 30) // 2,
                                      desc_w, 36,
                                      size=20, weight=500, color=label_col,
                                      align="left", family=hfam))
                # Divider line after each row except the last.
                if idx < n - 1:
                    blocks_out.append({
                        "id": f"rm-div-{idx}", "type": "shape", "content": "",
                        "position": {"x": phase_x, "y": by + row_h + gap // 2 - 1,
                                     "w": W - phase_x - margin_x, "h": 1},
                        "styling": {
                            "font_family": "", "font_size": 0, "font_weight": 0,
                            "color": divider_col, "background_color": divider_col,
                            "text_align": "left",
                        },
                    })

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
                              size=52, weight=800, color=heading_color,
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
                              size=52, weight=800, color=heading_color,
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
                              size=52, weight=800, color=heading_color,
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
                              size=56, weight=800, color=heading_color,
                              align="left", family=hfam))
        blocks_out.append(_accent_bar(60, 158, 100, 4))

        n       = min(len(bullets_raw), 4)
        # Wider step "tracks" — each gets a roomy column for circle + title +
        # description so the flow reads as 4 connected stages, not 4 tiny dots.
        step_w  = (W - 160) // n     # 280 for n=4
        start_x = 80
        circle_size = 96
        circle_y = 240
        desc_label_col = "#FAFAFA" if is_dark else "#0A0A0A"
        desc_body_col  = "rgba(255,255,255,0.78)" if is_dark else "rgba(0,0,0,0.65)"

        # Continuous horizontal rail behind the circles — connects the flow.
        rail_y = circle_y + circle_size // 2 - 1
        blocks_out.append({
            "id": "step-rail", "type": "shape", "content": "",
            "position": {"x": start_x + circle_size // 2,
                         "y": rail_y,
                         "w": (n - 1) * step_w, "h": 2},
            "styling": {
                "font_family": "", "font_size": 0, "font_weight": 0,
                "color": accent, "background_color": accent + "55", "text_align": "left",
            },
        })

        for idx, btext in enumerate(bullets_raw[:n]):
            cx = start_x + idx * step_w
            # Numbered circle — accent color, prominent.
            blocks_out.append({
                "id": f"step-num-{idx}", "type": "process_circle",
                "content": str(idx + 1),
                "position": {"x": cx + (step_w - circle_size) // 2,
                             "y": circle_y, "w": circle_size, "h": circle_size},
                "styling": {
                    "font_family": hfam, "font_size": 40, "font_weight": 800,
                    "color": "#ffffff", "background_color": accent, "text_align": "center",
                },
            })

            # Split "Title: Description" if a colon exists; otherwise the
            # entire bullet becomes the body.
            parts = btext.split(": ", 1) if ": " in btext else [btext, ""]
            title_line = parts[0].strip()
            body_line  = parts[1].strip() if len(parts) > 1 else ""

            # Step title — bold, under the circle.
            blocks_out.append(_b(f"step-title-{idx}", "text", title_line,
                                  cx + 10, circle_y + circle_size + 24,
                                  step_w - 20, 32,
                                  size=18, weight=700, color=desc_label_col,
                                  align="center", family=hfam))
            # Step description — softer body text.
            if body_line:
                blocks_out.append(_b(f"step-{idx}", "text", body_line,
                                      cx + 10, circle_y + circle_size + 64,
                                      step_w - 20, 140,
                                      size=14, weight=400, color=desc_body_col,
                                      align="center", family=bfam))

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
                              size=56, weight=800, color=heading_color,
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
_BULLET_MAX_WORDS = 12  # Gamma-tight. Was 16; cut to enforce editorial voice.
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
        max_concurrency: int | None = None,
    ) -> list[dict]:
        # Env-tunable concurrency. Default raised from 3 → 8 so 15-slide decks
        # finish content generation in ~15s instead of ~25s.
        if max_concurrency is None:
            max_concurrency = max(1, int(settings.SLIDE_GEN_CONCURRENCY))

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

        # Kick off backdrop fetches in parallel with slide-content generation.
        # By the time _build_slides runs, most/all backdrop tasks have completed
        # — so editor_background attach adds ~0s to wall-clock.
        backdrop_tasks: dict[str, asyncio.Task[str]] = {}
        if settings.EDITOR_BACKDROPS_ENABLED:
            theme_colors = mapping.theme.colors
            needed_variants = {
                v for v in (_backdrop_variant(item.get("type", "content")) for item in outline) if v
            }
            for variant in needed_variants:
                backdrop_tasks[variant] = asyncio.create_task(
                    backdrop_service.get_backdrop(theme_colors, variant)
                )

        logger.info(
            f"Generating {len(outline)} slides "
            f"(concurrency={max_concurrency}, backdrops={'on' if backdrop_tasks else 'off'})"
        )
        t0 = time.perf_counter()
        contents = await asyncio.gather(*[_throttled(item) for item in outline])
        t_content = time.perf_counter() - t0

        t1 = time.perf_counter()
        slides = await self._build_slides(outline, list(contents), mapping, logo_url, backdrop_tasks)
        t_build = time.perf_counter() - t1

        logger.info(
            f"Slide generation timings: content={t_content:.2f}s "
            f"build+backdrops={t_build:.2f}s total={t_content + t_build:.2f}s"
        )
        return slides

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

    async def _build_slides(
        self,
        outline: list[dict],
        contents: list[dict],
        mapping: TemplateMappingResult,
        logo_url: str,
        backdrop_tasks: dict[str, "asyncio.Task[str]"] | None = None,
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

            slide_dict: dict = {
                "order":      outline_item.get("order", i + 1),
                "type":       slide_type,
                "background": background,
                "blocks":     blocks,
            }

            # Editor-only photographic backdrop. Exporters ignore this field
            # and continue rendering `background` (color/gradient). Awaiting
            # here is cheap — tasks were started in run() and most have
            # already resolved during slide-content generation.
            if backdrop_tasks is not None:
                variant = _backdrop_variant(slide_type)
                task = backdrop_tasks.get(variant)
                if task is not None:
                    try:
                        image_url = await task
                    except Exception as exc:
                        logger.warning(f"Backdrop task for {variant} failed: {exc}")
                        image_url = ""
                    if image_url:
                        slide_dict["editor_background"] = {
                            "image": image_url,
                            "overlay": backdrop_service.overlay_for(variant),
                        }

            result.append(slide_dict)

        return result
