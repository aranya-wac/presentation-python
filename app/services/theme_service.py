from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.theme import Theme


async def resolve_theme(db: AsyncSession, theme_id: Optional[str]) -> Optional[Theme]:
    """Resolve an incoming `theme_id` value to a real Theme row.

    The frontend ThemePicker uses preset slugs like 'gamma-midnight' while the
    DB stores UUIDs and display names like 'Gamma Midnight'. Older code paths
    persisted the slug directly into `presentations.theme_id`, producing rows
    that fail FK lookups at export time. This helper accepts any of:
      - UUID matching themes.id
      - exact name match (e.g. 'Gamma Midnight')
      - preset slug ('gamma-midnight' → 'Gamma Midnight')

    Returns the Theme row, or None if nothing matched.
    """
    if not theme_id:
        return None

    theme = None
    try:
        theme = (
            await db.execute(select(Theme).where(Theme.id == theme_id))
        ).scalars().first()
    except Exception:
        # Some DBs reject malformed UUIDs at the driver layer; fall through.
        pass

    if theme is None:
        normalized = theme_id.replace("-", " ").replace("_", " ").strip().title()
        theme = (
            await db.execute(select(Theme).where(Theme.name == normalized))
        ).scalars().first()

    return theme


async def resolve_theme_id(db: AsyncSession, theme_id: Optional[str]) -> Optional[str]:
    """Return the canonical themes.id UUID for a slug/UUID/name input."""
    theme = await resolve_theme(db, theme_id)
    return str(theme.id) if theme else None


async def get_default_theme(db: AsyncSession) -> Optional[Theme]:
    """Return the project-wide fallback theme (Gamma Dark, else first row)."""
    theme = (
        await db.execute(select(Theme).where(Theme.name == "Gamma Dark"))
    ).scalars().first()
    if theme is None:
        theme = (await db.execute(select(Theme))).scalars().first()
    return theme
