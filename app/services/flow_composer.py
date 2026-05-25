"""
Flow composer — turns AI card *intents* into flow `Card` block trees.

The composer is purely STRUCTURAL: it lays out blocks (containers, callout
grids, icon lists, sub-sections, charts, tables, roadmaps, comparisons) and
sets sizing/spacing — but it does NOT bake in any colours or fonts. Colour and
typography are applied at render time by `CardRenderer` from the deck's theme,
so a deck can be re-themed instantly without regenerating.

Output shape matches `frontend/src/types/flow.ts` (`Card` / `FlowBlock`).
"""
from __future__ import annotations

import uuid
from typing import Any


# ── small builders ──────────────────────────────────────────────────────────

def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _clean(style: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in style.items() if v is not None}


def _leaf(type_: str, content: str | None = None, props: dict | None = None, **style: Any) -> dict:
    block: dict[str, Any] = {"id": _id(type_), "type": type_}
    if content is not None:
        block["content"] = content
    if props is not None:
        block["props"] = props
    cleaned = _clean(style)
    if cleaned:
        block["style"] = cleaned
    return block


def _container(type_: str, children: list[dict], **style: Any) -> dict:
    block: dict[str, Any] = {"id": _id(type_), "type": type_, "children": children}
    cleaned = _clean(style)
    if cleaned:
        block["style"] = cleaned
    return block


def _stack(children: list[dict], **style: Any) -> dict:
    return _container("stack", children, **style)


def _row(children: list[dict], **style: Any) -> dict:
    return _container("row", children, **style)


def _grid(cards: list[dict], gap: int, cols: int = 2) -> dict:
    """Lay leaf cards out in a `cols`-wide grid (a stack of rows)."""
    rows = [_row(cards[i:i + cols], gap=gap, align="stretch") for i in range(0, len(cards), cols)]
    return _stack(rows, gap=gap)


def _interleave_dividers(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, node in enumerate(nodes):
        if i > 0:
            out.append(_leaf("divider"))
        out.append(node)
    return out


# ── body composition (structure only — no colours) ──────────────────────────

def _callout(it: Any) -> dict:
    label = it.get("label", "") if isinstance(it, dict) else str(it)
    text = it.get("text", "") if isinstance(it, dict) else ""
    content = f"{label}\n{text}" if text else label
    return _leaf("callout", content, borderRadius=14, padding=20, flex=1)


def _icon_row(it: Any) -> dict:
    icon = it.get("icon", "sparkles") if isinstance(it, dict) else "sparkles"
    label = it.get("label", "") if isinstance(it, dict) else str(it)
    text = it.get("text", "") if isinstance(it, dict) else ""
    label_children = [_leaf("heading", label, fontSize=19, fontWeight=700)]
    if text:
        label_children.append(_leaf("text", text, fontSize=16, lineHeight=1.5))
    return _row(
        [
            _leaf("icon", props={"icon": icon}, minHeight=56),
            _stack(label_children, gap=3, flex=1),
        ],
        gap=18,
        align="center",
    )


def _compose_body(body: dict | None) -> dict | None:
    body = body or {}
    kind = body.get("kind", "paragraph")
    items = body.get("items")

    if kind == "callouts":
        cards = [_callout(it) for it in (items or [])]
        if not cards:
            return None
        return _stack(cards, gap=14) if len(cards) <= 2 else _grid(cards, gap=16, cols=2)

    if kind == "iconlist":
        rows = [_icon_row(it) for it in (items or [])]
        return _stack(rows, gap=18) if rows else None

    if kind == "subsections":
        secs: list[dict] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            secs.append(_stack(
                [
                    _leaf("heading", it.get("heading", ""), fontSize=20, fontWeight=700),
                    _leaf("text", it.get("text", ""), fontSize=17, lineHeight=1.55),
                ],
                gap=6,
            ))
        return _stack(_interleave_dividers(secs), gap=16) if secs else None

    if kind == "bullets":
        lines = [str(x) for x in (items or []) if str(x).strip()]
        return _leaf("bullets", "\n".join(lines), fontSize=19, lineHeight=1.5) if lines else None

    if kind == "stat":
        stats = [
            _leaf("stat", f"{it.get('value', '')}\n{it.get('label', '')}", flex=1)
            for it in (items or [])
            if isinstance(it, dict)
        ]
        return _grid(stats, gap=20, cols=2) if stats else None

    if kind == "chart":
        data = items.get("data", []) if isinstance(items, dict) else []
        ctype = items.get("chartType", "bar") if isinstance(items, dict) else "bar"
        return _leaf("chart", props={"chartType": ctype, "chartData": data}, minHeight=340)

    if kind == "table":
        headers = items.get("headers", []) if isinstance(items, dict) else []
        rows = items.get("rows", []) if isinstance(items, dict) else []
        if not headers and not rows:
            return None
        return _leaf("table", props={"tableData": {"headers": headers, "rows": rows}})

    if kind == "roadmap":
        phases: list[dict] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            phases.append(_stack(
                [
                    _leaf("heading", it.get("phase", ""), fontSize=15, fontWeight=800),
                    _leaf("text", it.get("label", ""), fontSize=16, lineHeight=1.45),
                ],
                gap=6,
                flex=1,
            ))
        return _row(phases, gap=22, align="start") if phases else None

    if kind == "comparison":
        if not isinstance(items, dict):
            return None

        def _side(d: Any) -> dict | None:
            if not isinstance(d, dict):
                return None
            side_rows = [str(x) for x in (d.get("items") or []) if str(x).strip()]
            return _stack(
                [
                    _leaf("heading", d.get("label", ""), fontSize=20, fontWeight=700),
                    _leaf("bullets", "\n".join(side_rows), fontSize=17, lineHeight=1.5),
                ],
                gap=10,
                padding=22,
                borderRadius=14,
                flex=1,
            )

        sides = [s for s in (_side(items.get("left")), _side(items.get("right"))) if s]
        return _row(sides, gap=20, align="stretch") if sides else None

    # paragraph / none / unknown
    if isinstance(items, str):
        text = items
    elif isinstance(items, list):
        text = " ".join(str(x) for x in items)
    else:
        text = ""
    return _leaf("text", text, fontSize=19, lineHeight=1.6) if text.strip() else None


# ── card + deck composition ─────────────────────────────────────────────────

# Body kinds laid out two-column beside the heading (chart/table). Roadmap and
# comparison need full width — never squeezed beside an image.
_VISUAL_KINDS = {"chart", "table"}
_FULLWIDTH_KINDS = {"roadmap", "comparison"}


def compose_card(
    intent: dict,
    index: int,
    total: int,
    background_url: str | None = None,
    image_url: str | None = None,
) -> dict:
    """Compose one AI card intent into a flow `Card` dict (structure only)."""
    role = intent.get("role", "content")
    is_title = role == "title" or index == 0
    is_closing = role == "closing" or index == total - 1

    body = intent.get("body") or {}
    body_kind = body.get("kind", "paragraph")

    heading = _leaf(
        "heading", intent.get("title", ""),
        fontSize=54 if is_title else 36, fontWeight=800, lineHeight=1.15,
    )
    intro_text = (intent.get("intro") or "").strip()
    intro = _leaf("text", intro_text, fontSize=22 if is_title else 19, lineHeight=1.55) if intro_text else None
    body_node = _compose_body(body)

    if body_kind in _VISUAL_KINDS and body_node is not None:
        text_children = [heading] + ([intro] if intro else [])
        text_col = _stack(text_children, gap=16, justify="center", flex=0.92)
        body_node.setdefault("style", {})["flex"] = 1.08
        pair = [text_col, body_node] if index % 2 == 0 else [body_node, text_col]
        inner = _row(pair, gap=44, align="center")
    elif image_url and body_kind not in _FULLWIDTH_KINDS:
        content_children = [heading] + ([intro] if intro else [])
        if body_node is not None:
            content_children.append(body_node)
        content_stack = _stack(content_children, gap=18, justify="center", flex=1.05)
        image = _leaf("image", props={"src": image_url}, borderRadius=16, minHeight=340, flex=0.95)
        pair = [content_stack, image] if index % 2 == 0 else [image, content_stack]
        inner = _row(pair, gap=44, align="center")
    else:
        children = [heading] + ([intro] if intro else [])
        if body_node is not None:
            children.append(body_node)
        inner = _stack(children, gap=20, justify="center" if (is_title or is_closing) else "start")

    root = _stack([inner], padding=72, justify="center")

    # The atmospheric backdrop image (when generated) is the card background;
    # otherwise the renderer fills it from the theme. The legibility overlay is
    # NOT baked — the renderer tints it from the theme so a re-theme adapts it.
    background = {"type": "image", "value": background_url} if background_url else None

    return {
        "id": _id("card"),
        "order": index + 1,
        "background": background,
        "root": root,
        "notes": intent.get("notes"),
    }


def compose_deck(
    generation: dict,
    background_url: str | None = None,
    image_urls: dict[int, str] | None = None,
) -> list[dict]:
    """Compose the AI's full generation output into a list of flow `Card` dicts."""
    cards = generation.get("cards", []) or []
    image_urls = image_urls or {}
    total = len(cards)
    return [
        compose_card(intent, i, total, background_url, image_urls.get(i))
        for i, intent in enumerate(cards)
        if isinstance(intent, dict)
    ]
