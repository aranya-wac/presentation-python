from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ExtractionError
from app.extractors.base_extractor import BaseExtractor


class PdfExtractor(BaseExtractor):
    def extract(self) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(self.file_path))
            pages_text = []
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    pages_text.append(text)
            return "\n\n".join(pages_text)
        except Exception as exc:
            raise ExtractionError(f"Failed to extract PDF: {exc}") from exc
