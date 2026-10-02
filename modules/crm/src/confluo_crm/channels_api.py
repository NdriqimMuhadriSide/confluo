"""Staff settings of the channels: web chat widget, WhatsApp. Mounted under /api/crm."""

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from confluo_core.deps import Tenant, TenantContext, requires
from confluo_core.secrets import SecretsNotConfigured, store_secret
from confluo_core.settings import Settings
from confluo_crm.channels.whatsapp import SECRET_PURPOSE

router = APIRouter(tags=["crm-channels"])

Manage = Annotated[TenantContext, Depends(requires("crm.settings.manage"))]
LANGS = {"en", "nl", "fr", "de", "sq"}


class WebChatSettings(BaseModel):
    color: str = Field("#111827", pattern=r"^#[0-9a-fA-F]{6}$")
    title: dict[str, str] = {}
    greeting: dict[str, str] = {}
    logo_url: str | None = Field(None, max_length=500, pattern=r"^https://")
    # Websites allowed to embed the widget, e.g. "https://www.kapsalon.be". Empty: any.
    allowed_origins: list[str] = []

    @field_validator("title", "greeting")
    @classmethod
    def _langs(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - LANGS:
            raise ValueError(f"unknown languages: {sorted(set(value) - LANGS)}")
        return {k: v.strip()[:300] for k, v in value.items() if v.strip()}

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        out = []
        for origin in value:
            origin = origin.strip().rstrip("/")
            if not origin:
                continue
            if not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
                raise ValueError(f"origin must be https: {origin}")
            out.append(origin)
        return out


class WebChat(BaseModel):
    enabled: bool
    key: str | None
    settings: WebChatSettings


@router.get("/channels/web", operation_id="getWebChat")
async def get_web_chat(tenant: Tenant) -> WebChat:
    cur = await tenant.conn.execute(
        "select external_account_id, status, settings from crm_channel_connection"
        " where tenant_id = %s and channel = 'web' order by created_at limit 1",
        (tenant.tenant_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return WebChat(enabled=False, key=None, settings=WebChatSettings())
    return WebChat(
        enabled=row[1] == "active", key=row[0], settings=WebChatSettings(**(row[2] or {}))
    )


class WebChatUpdate(BaseModel):
    enabled: bool
    settings: WebChatSettings


@router.put("/channels/web", operation_id="updateWebChat")
async def update_web_chat(body: WebChatUpdate, tenant: Manage) -> WebChat:
    status_ = "active" if body.enabled else "paused"
    settings: dict[str, Any] = body.settings.model_dump()
    cur = await tenant.conn.execute(
        "update crm_channel_connection set status = %s, settings = %s"
        " where tenant_id = %s and channel = 'web'",
        (status_, Jsonb(settings), tenant.tenant_id),
    )
    if cur.rowcount == 0:
        # First time: a public key for the embed snippet (not a secret).
        await tenant.conn.execute(
            "insert into crm_channel_connection (channel, external_account_id, status, settings)"
            " values ('web', %s, %s, %s)",
            (f"wk_{secrets.token_urlsafe(18)}", status_, Jsonb(settings)),
        )
    return await get_web_chat(tenant)


# --- WhatsApp ---------------------------------------------------------------------------


class WhatsApp(BaseModel):
    enabled: bool
    phone_number_id: str | None
    display_phone: str | None
    has_token: bool  # the access token is stored (never sent back)
    webhook_ready: bool  # Confluo's Meta app secret and verify token are configured


class WhatsAppUpdate(BaseModel):
    enabled: bool
    phone_number_id: str = Field(pattern=r"^\d{5,30}$")
    display_phone: str | None = Field(None, max_length=40)
    access_token: str | None = Field(None, min_length=20, max_length=1000)  # None: keep


@router.get("/channels/whatsapp", operation_id="getWhatsApp")
async def get_whatsapp(tenant: Tenant, request: Request) -> WhatsApp:
    cfg: Settings = request.app.state.settings
    cur = await tenant.conn.execute(
        "select external_account_id, status, settings, credentials_ref from crm_channel_connection"
        " where tenant_id = %s and channel = 'whatsapp' order by created_at limit 1",
        (tenant.tenant_id,),
    )
    row = await cur.fetchone()
    ready = bool(cfg.whatsapp_app_secret and cfg.whatsapp_verify_token)
    if row is None:
        return WhatsApp(
            enabled=False,
            phone_number_id=None,
            display_phone=None,
            has_token=False,
            webhook_ready=ready,
        )
    return WhatsApp(
        enabled=row[1] == "active",
        phone_number_id=row[0],
        display_phone=(row[2] or {}).get("display_phone"),
        has_token=row[3] is not None,
        webhook_ready=ready,
    )


@router.put("/channels/whatsapp", operation_id="updateWhatsApp")
async def update_whatsapp(body: WhatsAppUpdate, tenant: Manage, request: Request) -> WhatsApp:
    cfg: Settings = request.app.state.settings
    conn = tenant.conn
    cur = await conn.execute(
        "select id, credentials_ref from crm_channel_connection"
        " where tenant_id = %s and channel = 'whatsapp'",
        (tenant.tenant_id,),
    )
    row = await cur.fetchone()
    ref = row[1] if row else None
    if body.access_token:
        try:
            secret_id = await store_secret(
                conn,
                cfg,
                SECRET_PURPOSE,
                {"access_token": body.access_token.strip()},
                secret_id=UUID(ref) if ref else None,
            )
        except SecretsNotConfigured as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
        ref = str(secret_id)
    if ref is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The access token is required")
    settings = Jsonb({"display_phone": body.display_phone})
    try:
        async with conn.transaction():
            if row is None:
                await conn.execute(
                    "insert into crm_channel_connection"
                    " (channel, external_account_id, credentials_ref, status, settings)"
                    " values ('whatsapp', %s, %s, %s, %s)",
                    (body.phone_number_id, ref, "active" if body.enabled else "paused", settings),
                )
            else:
                await conn.execute(
                    "update crm_channel_connection set external_account_id = %s,"
                    " credentials_ref = %s, status = %s, settings = %s where id = %s",
                    (
                        body.phone_number_id,
                        ref,
                        "active" if body.enabled else "paused",
                        settings,
                        row[0],
                    ),
                )
    except errors.UniqueViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This WhatsApp number is connected to another business"
        ) from None
    return await get_whatsapp(tenant, request)
