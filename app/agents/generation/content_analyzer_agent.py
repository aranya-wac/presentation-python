from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ai import gemini_client
from app.ai.prompt_templates import CONTENT_ANALYSIS_PROMPT, render
from app.extractors.extractor_factory import extract_content
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ContentAnalyzerAgent:
    """Extracts text from a document and uses Gemini to analyze its content."""

    async def run(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        logger.info(f"Extracting content from {path.name}")
        content = extract_content(path)

        # Truncate to avoid token limits (~50k chars ~ 12k tokens)
        if len(content) > 50_000:
            content = content[:50_000] + "\n...[content truncated]"

        prompt = render(CONTENT_ANALYSIS_PROMPT, content=content)
        logger.info("Sending content to Gemini for analysis")
        analysis = await gemini_client.generate_json(prompt)
        logger.info("Content analysis complete")
        return analysis
