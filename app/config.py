from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


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
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
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
