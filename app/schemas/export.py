from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ExportRequest(BaseModel):
    format: Literal["pdf", "pptx", "html"]


class ExportJobResponse(BaseModel):
    job_id: str
    presentation_id: str
    format: str
    status: str
    progress: int
    file_url: Optional[str] = None
    file_size: Optional[int] = None
    error_message: Optional[str] = None
