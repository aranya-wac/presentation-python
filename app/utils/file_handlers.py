from __future__ import annotations

import os
from pathlib import Path


def safe_filename(filename: str) -> str:
    """Sanitize a filename by removing path traversal components."""
    return os.path.basename(filename).replace("..", "").strip()


def get_file_size(path: Path) -> int:
    """Return file size in bytes."""
    return path.stat().st_size


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
