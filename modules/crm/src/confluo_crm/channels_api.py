"""Staff settings of the web chat widget. Mounted under /api/crm."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from confluo_core.deps import Tenant, TenantContext, requires

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
