from __future__ import annotations

import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource


class Settings(BaseSettings):
    MYSQL_URL: str = "mysql+aiomysql://root:password@localhost:3306/wacdeckstudio"
    DATABASE_URL: str = "sqlite+aiosqlite:///./wacdeckstudio.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    GEMINI_API_KEY: str = ""
    # Optional: when set, the topic-research feature uses Serper.dev for
    # higher-quality news results. When empty, it falls back to a
    # DuckDuckGo HTML scrape.
    SERPER_API_KEY: str = ""
    # When set, the editor-backdrop service fetches photographic backgrounds
    # from Unsplash. Empty → PIL fallback (vectorized radial gradient).
    UNSPLASH_ACCESS_KEY: str = ""
    # Max parallel Gemini slide-content calls. Higher → faster generation,
    # at the cost of hitting rate limits sooner. Default tuned for ≤15 slides
    # finishing under 40s wall-clock.
    SLIDE_GEN_CONCURRENCY: int = 8
    # Master switch for editor backdrops. Off → no backdrop service call,
    # no editor_background field emitted. Lets us A/B and bypass for
    # brand-strict themes.
    EDITOR_BACKDROPS_ENABLED: bool = True
    # Use Gemini Nano Banana to generate bespoke cinematic backdrops for
    # hero/closing slides. Requires a PAID Gemini key (free tier quota is
    # too small for image gen). Off by default → Unsplash handles heroes too.
    # Set to true after upgrading Gemini plan for Gamma-tier hero imagery.
    AI_HERO_BACKDROPS_ENABLED: bool = False
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    MAX_UPLOAD_SIZE_MB: int = 10
    # URL fetch safety (used by /generate/sync `url` field)
    MAX_URL_BYTES_MB: int = 5
    URL_FETCH_TIMEOUT_SECONDS: float = 20.0
    URL_FETCH_MAX_REDIRECTS: int = 5
    # When True, skip private/loopback/link-local IP rejection. Dev/test only.
    URL_FETCH_ALLOW_PRIVATE: bool = False
    # When False (default), document extraction runs synchronously in the
    # request thread (dev/no-worker). When True, extraction is dispatched
    # to a Celery worker — the worker MUST be running, since `apply_async`
    # to a reachable broker without a worker would leave docs stuck in
    # "pending" forever.
    USE_EXTRACTION_WORKER: bool = False
    # NoDecode prevents pydantic-settings from JSON-parsing the env var before
    # our validator runs, so plain strings like "https://foo.vercel.app" or
    # comma-separated "https://a.com,https://b.com" don't crash on startup.
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            s = v.strip()
            # Still accept a JSON-encoded list for the .env file form.
            if s.startswith("["):
                try:
                    return json.loads(s)
                except json.JSONDecodeError:
                    pass
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v):
        # Render (and Heroku) hand out connection strings as `postgres://...`,
        # but SQLAlchemy needs `postgresql+asyncpg://...` for the async driver.
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return "postgresql+asyncpg://" + v[len("postgres://"):]
            if v.startswith("postgresql://"):
                return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    model_config = {"env_file": ".env"}

    @classmethod
    def settings_customise_sources(cls, settings_cls: type[BaseSettings], **kwargs) -> tuple[PydanticBaseSettingsSource, ...]:
        # .env file takes priority over system environment variables
        init = kwargs.get("init_settings")
        dotenv = kwargs.get("dotenv_settings")
        env = kwargs.get("env_settings")
        secrets = kwargs.get("secrets_settings") or kwargs.get("file_secret_settings")
        return tuple(s for s in [init, dotenv, env, secrets] if s is not None)


settings = Settings()
