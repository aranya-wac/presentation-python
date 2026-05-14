from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ExtractionError
from app.extractors.base_extractor import BaseExtractor


class TextExtractor(BaseExtractor):
    def extract(self) -> str:
        try:
            return self.file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ExtractionError(f"Failed to extract text file: {exc}") from exc
