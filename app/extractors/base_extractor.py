from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BaseExtractor(ABC):
    def __init__(self, file_path: Path):
        self.file_path = file_path

    @abstractmethod
    def extract(self) -> str:
        """Extract text content from the file and return as string."""
        ...
