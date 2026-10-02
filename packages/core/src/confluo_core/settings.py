from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration, read from the environment (and `.env` in local dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CONFLUO_", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    # The API and worker connect as `confluo_app`, a non-owner role that RLS applies to.
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://confluo_app:confluo_app@127.0.0.1:54322/postgres"),
        description="App connection (role confluo_app). Supabase local stack by default.",
    )
    # Migrations run as the owner of the schema.
    migrations_database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://postgres:postgres@127.0.0.1:54322/postgres"),
        description="Owner connection used by Alembic and admin scripts.",
    )
    # Password `make migrate` gives the confluo_app role. Must match database_url.
    app_db_password: SecretStr = SecretStr("confluo_app")
    supabase_url: str = "http://127.0.0.1:54321"
    # Staff access tokens are Supabase Auth JWTs, verified against the project's public
    # signing keys (JWKS) — no shared secret. Audience is what Supabase puts in `aud`.
    supabase_jwt_audience: str = "authenticated"
    # Expected `iss` claim. Defaults to <supabase_url>/auth/v1; set it when the API
    # reaches Supabase under a different host than the one that issues tokens (Docker).
    supabase_jwt_issuer: str | None = None
    # Server-side Supabase key (sb_secret_...), used only to send invitation emails.
    # Without it invitations are still recorded; new users just get no email.
    supabase_secret_key: SecretStr | None = None

    api_host: str = "127.0.0.1"
    api_port: int = 8100
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    worker_heartbeat_seconds: float = 30.0

    # Background jobs: attempts in total, and the first retry delay (doubles each time).
    job_max_attempts: int = Field(5, ge=1, le=20)
    job_retry_base_seconds: float = Field(10.0, ge=0)

    # LLM gateway: "provider:model" per tier. Providers: anthropic, voyage, fake.
    llm_fast: str = "anthropic:claude-haiku-4-5"  # intent, extraction, classification
    llm_dialogue: str = "anthropic:claude-opus-5"  # customer-facing replies
    llm_embedding: str = "voyage:voyage-3.5"  # knowledge base (1024 dims)
    # Provider keys use the vendors' usual variable names.
    anthropic_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CONFLUO_ANTHROPIC_API_KEY")
    )
    voyage_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("VOYAGE_API_KEY", "CONFLUO_VOYAGE_API_KEY")
    )

    # Key for encrypted secrets (OAuth tokens): 32 bytes, base64. Generate with
    # `uv run python -c "from confluo_core.secrets import new_key; print(new_key())"`.
    secrets_key: SecretStr | None = None
    # Public HTTPS base URL of this API, when reachable from the internet; enables
    # provider push notifications (e.g. Microsoft Graph subscriptions).
    public_api_url: str | None = None
    # Dashboard base URL (OAuth redirects come back here).
    dashboard_url: str = "http://localhost:3000"

    # Microsoft 365 calendar sync (Microsoft Entra app registration).
    ms_client_id: str | None = None
    ms_client_secret: SecretStr | None = None
    ms_authority: str = "https://login.microsoftonline.com/common"
    ms_graph_url: str = "https://graph.microsoft.com/v1.0"

    # WhatsApp Business (Cloud API): Confluo's Meta app. Each business's phone number
    # and access token are stored per channel connection (Settings → Channels).
    whatsapp_app_secret: SecretStr | None = None  # signs webhook deliveries
    whatsapp_verify_token: str | None = None  # echoed when Meta verifies the webhook
    whatsapp_graph_url: str = "https://graph.facebook.com/v23.0"

    # Shared secret of the built-in `test` webhook provider (local/test only).
    webhook_test_secret: SecretStr | None = None

    @property
    def supabase_auth_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwt_issuer(self) -> str:
        return self.supabase_jwt_issuer or self.supabase_auth_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
