from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class PositionSchema(BaseModel):
    x: float
    y: float
    w: float
    h: float


class StylingSchema(BaseModel):
    font_family: str = ""
    font_size: int = 16
    font_weight: int = 400
    color: str = "#000000"
    background_color: str = "transparent"
    text_align: str = "left"


class ChartDataPointSchema(BaseModel):
    label: str
    value: float


class BlockSchema(BaseModel):
    # Block payloads carry block-type-specific extras (chart_type/chart_data
    # today, possibly more later). Preserve them on round-trip so saves don't
    # silently strip chart/image/roadmap data.
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    content: str = ""
    position: PositionSchema
    styling: StylingSchema = StylingSchema()
    chart_type: Optional[Literal["bar", "line", "pie"]] = None
    chart_data: Optional[list[ChartDataPointSchema]] = None


class SlideBackgroundSchema(BaseModel):
    type: str = "color"
    value: str = "#ffffff"


class EditorBackgroundSchema(BaseModel):
    """Editor-only backdrop image. Exporters ignore this field by design —
    PPTX/PDF/HTML output continues to use `background` (color/gradient)."""
    image: str = ""
    overlay: Optional[str] = None


class SlideSchema(BaseModel):
    order: int
    type: str
    background: Optional[SlideBackgroundSchema] = None
    editor_background: Optional[EditorBackgroundSchema] = None
    blocks: list[BlockSchema] = []
    notes: str = ""


class DeckLayoutSchema(BaseModel):
    id: str
    name: str
    blocks: list[BlockSchema] = []


class ThemeSchema(BaseModel):
    id: str
    name: str
    colors: dict[str, Any] = {}
    fonts: dict[str, Any] = {}


class PresentationListItem(BaseModel):
    id: str
    title: str
    description: str
    template_id: str
    template_name: str
    theme_id: str
    is_preview: bool
    total_slides: int
    slide_count: int
    token_count: int = 0
    created_at: str
    updated_at: str = ""
    # "slides" = legacy absolute-positioned deck; "flow" = Gamma flow deck.
    format: str = "slides"
    # A legacy SlideSchema OR a flow Card dict, depending on `format`.
    preview_slide: Optional[Any] = None


class PresentationDetail(PresentationListItem):
    # Legacy decks: list of SlideSchema-shaped dicts. Flow decks: list of flow
    # Card dicts. Typed loosely so both round-trip without coercion.
    slides: list[Any]
    logo_url: str
    layouts: list[Any] = []
    # The deck's resolved theme (colors + fonts) so the flow editor can render
    # and re-theme without a second request. None when the theme row is gone.
    theme: Optional[ThemeSchema] = None


class UpdatePresentationRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    theme_id: Optional[str] = None
    slides: Optional[list[SlideSchema]] = None
    layouts: Optional[list[DeckLayoutSchema]] = None
    token_count: Optional[int] = None


class CreatePresentationRequest(BaseModel):
    title: str
    description: str = ""
    slides: list[SlideSchema]
    theme_id: str
    template_id: str = ""
    logo_url: str = ""
    token_count: int = 0


class CreateFlowPresentationRequest(BaseModel):
    """Save a generated Gamma flow deck. `cards` is a list of flow `Card` dicts."""
    title: str
    cards: list[dict[str, Any]]
    theme_id: Optional[str] = None
    token_count: int = 0


class UpdateFlowPresentationRequest(BaseModel):
    """Persist content edits to a saved Gamma flow deck."""
    cards: list[dict[str, Any]]
    title: Optional[str] = None
    theme_id: Optional[str] = None
    layouts: Optional[list[dict[str, Any]]] = None
