from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from app.config import settings
from app.core.exceptions import ValidationError

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOADS_DIR = BASE_DIR / "storage" / "uploads"
EXPORTS_DIR = BASE_DIR / "storage" / "exports"
LOGS_DIR = BASE_DIR / "storage" / "logs"


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


async def save_upload(job_id: str, filename: str, file: UploadFile) -> Path:
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    dest_dir = UPLOADS_DIR / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    total = 0
    with dest.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 64)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                dest.unlink(missing_ok=True)
                raise ValidationError(
                    f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB"
                )
            f.write(chunk)

    return dest


def delete_upload(job_id: str) -> None:
    target = UPLOADS_DIR / job_id
    if target.exists():
        shutil.rmtree(target)


def export_path(presentation_id: str, fmt: str) -> Path:
    dest_dir = EXPORTS_DIR / presentation_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext_map = {"pdf": "pdf", "pptx": "pptx", "html": "html"}
    ext = ext_map.get(fmt, fmt)
    return dest_dir / f"presentation.{ext}"
