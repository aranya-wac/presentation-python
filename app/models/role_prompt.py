from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedModel


class RolePromptProfile(TimestampedModel):
    """Editable per-role generation profile.

    Seeded with hardcoded defaults on first run; admins can override the
    `audience`, `focus`, and `prompt_template` columns via direct DB updates
    or a future admin endpoint. The `role` field is the lookup key — one row
    per supported role.
    """
    __tablename__ = "role_prompt_profiles"

    role: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    focus: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional override for the prompt skeleton. When NULL, the service falls
    # back to its built-in template and only substitutes audience + focus.
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
