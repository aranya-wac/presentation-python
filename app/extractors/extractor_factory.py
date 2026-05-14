from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ExtractionError
from app.extractors.base_extractor import BaseExtractor
from app.extractors.docx_extractor import DocxExtractor
from app.extractors.pdf_extractor import PdfExtractor
from app.extractors.text_extractor import TextExtractor


def get_extractor(file_path: Path) -> BaseExtractor:
    ext = file_path.suffix.lower()
    if ext == ".txt":
        return TextExtractor(file_path)
    elif ext == ".docx":
        return DocxExtractor(file_path)
    elif ext == ".pdf":
        return PdfExtractor(file_path)
    else:
        raise ExtractionError(f"Unsupported file type: {ext}")


def extract_content(file_path: Path) -> str:
    extractor = get_extractor(file_path)
    return extractor.extract()
