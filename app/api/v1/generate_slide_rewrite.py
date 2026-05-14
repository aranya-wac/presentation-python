from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.user import User
from app.utils.logger import get_logger

router = APIRouter(prefix="/generate", tags=["generation"])
logger = get_logger(__name__)


PRESET_INSTRUCTIONS = {
    "shorter": (
        "Tighten the copy. Aim for roughly half the current word count while "
        "preserving every key fact. Prefer fewer, sharper bullets over many "
        "verbose ones. Keep all numeric values intact."
    ),
    "more_visual": (
        "Reduce the word count and lean on visual blocks. If the content "
        "supports it, add a `chart` block with chart_type and chart_data, or "
        "convert step-by-step content into process_circle blocks, or add a "
        "stat block with the most impactful number. Keep at most one heading."
    ),
    "add_data": (
        "Add concrete numbers, percentages, or comparisons where the topic "
        "supports them. If the source clearly contains numeric data, surface "
        "it in stat blocks. Do not invent statistics — if no real numbers "
        "fit, leave the slide alone and return it unchanged."
    ),
    "rephrase_execs": (
        "Rewrite in an executive-summary tone: outcomes first, no jargon, no "
        "filler. Lead with the bottom-line decision or implication. Cut "
        "qualifiers ('might', 'could possibly', 'in some cases'). Keep it "
        "scannable in under 10 seconds."
    ),
    "fix_grammar": (
        "Fix grammar, spelling, and clarity only. Do not restructure the slide "
        "or change the layout. Preserve every block id, position, type, and "
        "styling exactly. Only touch the `content` text fields where needed."
    ),
}


class SlideRewriteRequest(BaseModel):
    slide: dict[str, Any] = Field(..., description="The full current slide JSON")
    instruction: str = Field("", description="Free-text instruction OR one of the preset keys")
    preset: Optional[str] = Field(
        None,
        description="If set, takes precedence over `instruction`. One of: "
                    "shorter, more_visual, add_data, rephrase_execs, fix_grammar.",
    )


class SlideRewriteResponse(BaseModel):
    slide: dict[str, Any]
    note: str = ""


@router.post("/slide/rewrite", response_model=SlideRewriteResponse)
@limiter.limit("60/hour")
async def rewrite_slide(
    request: Request,
    payload: SlideRewriteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SlideRewriteResponse:
    """Rewrite a single slide given an instruction or preset.

    Returns the new slide JSON. Block ids, positions, types, and stylings are
    preserved unless the instruction explicitly requires structural changes
    (e.g. `more_visual` may swap a text block for a chart). The frontend just
    drops the result into place, so the response shape must match the editor's
    Slide schema exactly.
    """
    if not payload.slide or not isinstance(payload.slide, dict):
        raise HTTPException(status_code=400, detail="slide is required.")

    instruction = ""
    if payload.preset:
        if payload.preset not in PRESET_INSTRUCTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"preset must be one of {sorted(PRESET_INSTRUCTIONS)}.",
            )
        instruction = PRESET_INSTRUCTIONS[payload.preset]
    if payload.instruction:
        # Allow combining preset + free-text refinement
        instruction = (instruction + "\n\nAdditional guidance: " + payload.instruction).strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction or preset is required.")

    slide_json = json.dumps(payload.slide, indent=2)
    prompt = f"""You are an AI slide editor. Rewrite the slide below according to the instruction.

Current slide (JSON):
{slide_json}

Instruction:
{instruction}

STRICT rules:
- Return the FULL slide JSON (same top-level shape: order, type, background, blocks, notes).
- Preserve every block's `id`. Do not generate new ids — reuse the existing ones.
- Preserve `position` and `styling` exactly unless the instruction requires a layout change.
- Do not invent facts or statistics. If the slide has no source data and the
  instruction is "add_data", return the slide unchanged.
- If you change a block's type (e.g. text → chart), keep the same id.
- Return ONLY a JSON object with two keys: `slide` (the new slide) and
  `note` (1 sentence summary of what changed). No markdown fences.
"""

    try:
        result = await gemini_client.generate_json(prompt)
    except Exception as exc:
        logger.exception("Slide rewrite failed")
        raise HTTPException(status_code=502, detail=f"Rewrite failed: {exc}")

    new_slide = result.get("slide")
    if not isinstance(new_slide, dict) or "blocks" not in new_slide:
        raise HTTPException(status_code=502, detail="AI returned a malformed slide.")

    # Belt-and-braces: enforce top-level invariants so a bad response doesn't
    # corrupt the editor state.
    new_slide.setdefault("order", payload.slide.get("order", 1))
    new_slide.setdefault("type", payload.slide.get("type", "content"))
    if "background" not in new_slide and "background" in payload.slide:
        new_slide["background"] = payload.slide["background"]
    if "notes" not in new_slide and "notes" in payload.slide:
        new_slide["notes"] = payload.slide["notes"]

    return SlideRewriteResponse(slide=new_slide, note=str(result.get("note", ""))[:280])
