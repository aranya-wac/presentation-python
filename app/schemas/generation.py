from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GenerationStartRequest(BaseModel):
    template_id: str
    logo_url: Optional[str] = None


class GenerationJobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    presentation_id: Optional[str] = None
    error_message: Optional[str] = None


class GenerationStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    presentation_id: Optional[str] = None
    error_message: Optional[str] = None
