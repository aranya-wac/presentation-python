from __future__ import annotations

from sqlalchemy import String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class Theme(TimestampedModel):
    __tablename__ = "themes"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    colors: Mapped[dict] = mapped_column(JSON, nullable=False)  # {primary,secondary,accent,background,text}
    fonts: Mapped[dict] = mapped_column(JSON, nullable=False)   # {heading,body,caption} each {family,size,weight}
