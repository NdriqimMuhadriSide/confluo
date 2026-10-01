from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from the environment (and `.env` in local dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CONFLUO_", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://postgres:postgres@127.0.0.1:54322/postgres"),
        description="Postgres connection string (Supabase local stack by default).",
    )
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_jwt_secret: SecretStr | None = None

    api_host: str = "127.0.0.1"
    api_port: int = 8100
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    worker_heartbeat_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
