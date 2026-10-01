"""Connect a resource's external calendar. Mounted under /api/crm.

    GET    /calendar/microsoft/authorize?resource_id=…   the Microsoft sign-in URL
    POST   /calendar/microsoft/callback                  {code, state} from the redirect
    GET    /resources/{id}/calendar                      the connection (or null)
    POST   /resources/{id}/calendar/sync                 sync now
    DELETE /resources/{id}/calendar                      disconnect

Managers can connect any resource; a team member can connect their own (the
resource whose member_id is them). Tokens are stored encrypted (core `secret`).
"""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

import procrastinate
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from confluo_core.deps import Tenant, TenantContext
from confluo_core.jobs import defer_in
from confluo_core.secrets import SecretsNotConfigured, delete_secret, store_secret
from confluo_core.settings import Settings
from confluo_crm.calendar import microsoft as ms
from confluo_crm.calendar.sync import SECRET_PURPOSE, graph_for, load_connection

log = logging.getLogger("confluo.calendar")
router = APIRouter(tags=["crm-calendar"])


class CalendarConnectionOut(BaseModel):
    id: UUID
    resource_id: UUID
    provider: Literal["google", "microsoft"]
    account_email: str | None
    status: Literal["active", "error", "revoked"]
    last_synced_at: datetime | None
    last_error: str | None
    live_updates: bool  # Outlook pushes changes (else: synced every 5 minutes)


class AuthorizeOut(BaseModel):
    url: str


class CallbackIn(BaseModel):
    code: str
    state: str


async def _may_connect(tenant: TenantContext, resource_id: UUID) -> None:
    cur = await tenant.conn.execute(
        "select member_id from crm_resource where id = %s and tenant_id = %s",
        (resource_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such resource")
    if not tenant.can("crm.settings.manage") and row[0] != tenant.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your calendar")


async def _out(tenant: TenantContext, resource_id: UUID) -> CalendarConnectionOut | None:
    cur = await tenant.conn.execute(
        "select id, resource_id, provider, account_email, status, last_synced_at, last_error,"
        " subscription_id is not null and subscription_expires_at > now()"
        " from crm_calendar_connection where resource_id = %s",
        (resource_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return CalendarConnectionOut(
        id=row[0],
        resource_id=row[1],
        provider=row[2],
        account_email=row[3],
        status=row[4],
        last_synced_at=row[5],
        last_error=row[6],
        live_updates=row[7],
    )


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _job(request: Request, name: str) -> procrastinate.tasks.Task:  # type: ignore[type-arg]
    job_app: procrastinate.App = request.app.state.job_app
    return job_app.tasks[name]


@router.get("/calendar/microsoft/authorize", operation_id="microsoftCalendarAuthorize")
async def authorize(resource_id: UUID, tenant: Tenant, request: Request) -> AuthorizeOut:
    await _may_connect(tenant, resource_id)
    try:
        url = ms.authorize_url(_settings(request), tenant.tenant_id, tenant.user.id, resource_id)
    except (ms.NotConfigured, SecretsNotConfigured) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return AuthorizeOut(url=url)


@router.post("/calendar/microsoft/callback", operation_id="microsoftCalendarCallback")
async def callback(body: CallbackIn, tenant: Tenant, request: Request) -> CalendarConnectionOut:
    settings = _settings(request)
    try:
        state = ms.read_state(settings, body.state)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired sign-in") from None
    # The sign-in must finish in the business and by the person who started it.
    if state.tenant_id != tenant.tenant_id or state.user_id != tenant.user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired sign-in")
    await _may_connect(tenant, state.resource_id)
    try:
        tokens = await ms.exchange_code(settings, body.code, state.verifier)
        graph = ms.GraphClient(settings, tokens["access_token"])
        me = await graph.me()
        calendar_id = await graph.default_calendar()
    except (ms.AuthRevoked, ms.GraphError) as exc:
        log.warning("microsoft calendar sign-in failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Microsoft sign-in failed") from None

    account = me.get("mail") or me.get("userPrincipalName")
    conn = tenant.conn
    cur = await conn.execute(
        "select id, credentials_ref, account_email from crm_calendar_connection"
        " where resource_id = %s",
        (state.resource_id,),
    )
    old = await cur.fetchone()
    if old is not None:  # reconnecting: start fresh
        await conn.execute("delete from crm_calendar_connection where id = %s", (old[0],))
        await delete_secret(conn, UUID(old[1]))
    if old is None or old[2] != account:
        # Event ids of another account mean nothing here: bookings get new events.
        await conn.execute(
            "update crm_appointment set external_event_id = null where resource_id = %s",
            (state.resource_id,),
        )
    secret_id = await store_secret(conn, settings, SECRET_PURPOSE, tokens)
    cur = await conn.execute(
        "insert into crm_calendar_connection"
        " (resource_id, provider, calendar_id, credentials_ref, account_email)"
        " values (%s, 'microsoft', %s, %s, %s) returning id",
        (state.resource_id, calendar_id, str(secret_id), account),
    )
    row = await cur.fetchone()
    assert row is not None
    await defer_in(
        conn,
        _job(request, "crm:sync_calendar"),
        lock=f"calsync:{row[0]}",
        tenant_id=str(tenant.tenant_id),
        connection_id=str(row[0]),
    )
    # Upcoming confirmed bookings go into the newly connected calendar.
    await conn.execute(
        "select app.defer_job('crm:push_appointment',"
        " jsonb_build_object('tenant_id', tenant_id, 'appointment_id', id, 'deleted', false),"
        " 'cal:' || id)"
        " from crm_appointment where resource_id = %s and status = 'confirmed'"
        " and upper(during) > now()",
        (state.resource_id,),
    )
    out = await _out(tenant, state.resource_id)
    assert out is not None
    return out


@router.get("/resources/{resource_id}/calendar", operation_id="getResourceCalendar")
async def get_calendar(resource_id: UUID, tenant: Tenant) -> CalendarConnectionOut | None:
    await _may_connect(tenant, resource_id)
    return await _out(tenant, resource_id)


@router.post(
    "/resources/{resource_id}/calendar/sync",
    operation_id="syncResourceCalendar",
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_now(resource_id: UUID, tenant: Tenant, request: Request) -> Response:
    await _may_connect(tenant, resource_id)
    c = await load_connection(tenant.conn, resource_id=resource_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No calendar connected")
    await defer_in(
        tenant.conn,
        _job(request, "crm:sync_calendar"),
        lock=f"calsync:{c.id}",
        tenant_id=str(tenant.tenant_id),
        connection_id=str(c.id),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.delete(
    "/resources/{resource_id}/calendar",
    operation_id="disconnectResourceCalendar",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disconnect(resource_id: UUID, tenant: Tenant, request: Request) -> Response:
    await _may_connect(tenant, resource_id)
    conn = tenant.conn
    c = await load_connection(conn, resource_id=resource_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No calendar connected")
    if c.subscription_id and c.status == "active":
        try:
            graph = await graph_for(request.app.state.pool, _settings(request), c)
            await graph.unsubscribe(c.subscription_id)
        except (ms.AuthRevoked, ms.GraphError) as exc:
            log.info("calendar %s: unsubscribe failed (it expires anyway): %s", c.id, exc)
    # Busy times go with the connection (cascade); events already in Outlook stay.
    await conn.execute("delete from crm_calendar_connection where id = %s", (c.id,))
    await delete_secret(conn, UUID(c.credentials_ref))
    await conn.execute(
        "update crm_appointment set external_event_id = null where resource_id = %s",
        (resource_id,),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
