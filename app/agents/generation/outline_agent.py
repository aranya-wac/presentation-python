from __future__ import annotations

import json
from typing import Any

from app.ai import gemini_client
from app.ai.prompt_templates import OUTLINE_GENERATION_PROMPT, render
from app.utils.logger import get_logger

logger = get_logger(__name__)


class OutlineAgent:
    """Phase 1: generates a structured slide outline from document analysis."""

    async def run(self, analysis: dict[str, Any]) -> list[dict]:
        slide_count = analysis.get("estimated_slides", 10)
        prompt = render(
            OUTLINE_GENERATION_PROMPT,
            analysis=json.dumps(analysis, indent=2),
            slide_count=str(slide_count),
        )

        logger.info("Generating presentation outline with Gemini")
        outline = await gemini_client.generate_json(prompt)
        logger.info(f"Outline generated: {len(outline)} slides")
        return outline
