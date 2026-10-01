"""External calendar sync jobs (Microsoft 365 today; Google plugs in the same way).

    crm:sync_calendar        delta-read one connection's calendar into crm_external_busy
                             (and keep its change-notification subscription alive)
    crm:push_appointment     write one booking to its resource's calendar: create, move,
                             delete; deferred by a trigger on crm_appointment
    crm:sync_all_calendars   every 5 minutes: a sync for every active connection, the
                             safety net for missed notifications (and the only trigger
                             locally, where Microsoft can't reach the API)

Confirmed bookings become events; cancelled or unconfirmed ones lose theirs. Busy,
tentative and out-of-office events block time; free ones and Confluo's own don't.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import procrastinate
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from confluo_core.jobs import POOL_KEY, SETTINGS_KEY, JobDeps, tenant_task
from confluo_core.secrets import load_secret, store_secret
from confluo_core.settings import Settings, get_settings
from confluo_core.tenancy import tenant_transaction
from confluo_crm.calendar import microsoft as ms
from confluo_crm.knowledge import crm_tasks

log = logging.getLogger("confluo.calendar")

PAST = timedelta(days=1)
WINDOW = timedelta(days=180)
RESYNC_WITHIN = timedelta(days=30)  # restart the delta window when its end is this close
SUBSCRIPTION_LIFE = timedelta(days=3)  # Graph allows < 7 days for Outlook events
RENEW_WITHIN = timedelta(days=1)
SECRET_PURPOSE = "crm.calendar.microsoft"
WEBHOOK_PROVIDER = "microsoft-calendar"


@dataclass(frozen=True)
class Connection:
    id: UUID
    tenant_id: UUID
    resource_id: UUID
    calendar_id: str
    sync_token: str | None
    credentials_ref: str
    window_end: datetime | None
    subscription_id: str | None
    subscription_expires_at: datetime | None
    status: str


def _settings(deps: JobDeps) -> Settings:
    settings: Settings = deps.context.get(SETTINGS_KEY) or get_settings()
    return settings


async def load_connection(
    conn: AsyncConnection, *, connection_id: UUID | None = None, resource_id: UUID | None = None
) -> Connection | None:
    cur = conn.cursor(row_factory=dict_row)
    column, value = ("id", connection_id) if connection_id else ("resource_id", resource_id)
    await cur.execute(
        "select id, tenant_id, resource_id, calendar_id, sync_token, credentials_ref, window_end,"
        " subscription_id, subscription_expires_at, status"
        f" from crm_calendar_connection where {column} = %s",
        (value,),
    )
    row = await cur.fetchone()
    return Connection(**row) if row else None


async def graph_for(pool: AsyncConnectionPool, settings: Settings, c: Connection) -> ms.GraphClient:
    """A Graph client with a fresh access token. A refreshed token is stored in its own
    transaction: Microsoft rotates refresh tokens, so the new one must survive even if
    the job that needed it fails."""
    async with tenant_transaction(pool, c.tenant_id) as conn:
        tokens = await load_secret(conn, settings, UUID(c.credentials_ref))
    if ms.needs_refresh(tokens):
        tokens = await ms.refresh(settings, tokens["refresh_token"])
        async with tenant_transaction(pool, c.tenant_id) as conn:
            await store_secret(
                conn, settings, SECRET_PURPOSE, tokens, secret_id=UUID(c.credentials_ref)
            )
    return ms.GraphClient(settings, tokens["access_token"])


async def _mark(pool: AsyncConnectionPool, c: Connection, status: str, error: str) -> None:
    async with tenant_transaction(pool, c.tenant_id) as conn:
        await conn.execute(
            "update crm_calendar_connection set status = %s, last_error = %s where id = %s",
            (status, error[:500], c.id),
        )


# --- Read: Outlook busy time -> crm_external_busy -----------------------------------


@tenant_task(crm_tasks, name="sync_calendar", pass_deps=True)
async def sync_calendar(conn: AsyncConnection, deps: JobDeps, connection_id: str) -> None:
    c = await load_connection(conn, connection_id=UUID(connection_id))
    if c is None or c.status == "revoked":
        return
    settings = _settings(deps)
    try:
        graph = await graph_for(deps.pool, settings, c)
        await _sync(conn, graph, c)
        await ensure_subscription(conn, settings, graph, c)
    except ms.AuthRevoked as exc:
        # Retrying won't help: someone has to connect the calendar again.
        log.warning("calendar %s: access revoked: %s", c.id, exc)
        await conn.execute(
            "update crm_calendar_connection set status = 'revoked', last_error = %s where id = %s",
            (f"Access revoked: {exc}"[:500], c.id),
        )
    except Exception as exc:
        await _mark(deps.pool, c, "error", f"{type(exc).__name__}: {exc}")
        raise


async def _sync(conn: AsyncConnection, graph: ms.GraphClient, c: Connection) -> None:
    now = datetime.now(UTC)
    full = c.sync_token is None or c.window_end is None or c.window_end - now < RESYNC_WITHIN
    try:
        page = await graph.delta(
            c.calendar_id,
            delta_link=None if full else c.sync_token,
            start=now - PAST,
            end=now + WINDOW,
        )
    except ms.GraphError as exc:
        if exc.status != 410:  # 410 Gone: the delta token expired; start over
            raise
        full = True
        page = await graph.delta(c.calendar_id, delta_link=None, start=now - PAST, end=now + WINDOW)

    cur = await conn.execute(
        "select external_event_id from crm_appointment"
        " where resource_id = %s and external_event_id is not null",
        (c.resource_id,),
    )
    own = {r[0] for r in await cur.fetchall()}
    if full:
        # A full read lists every event in the window: start from a clean slate.
        await conn.execute(
            "delete from crm_external_busy where calendar_connection_id = %s", (c.id,)
        )
    for ch in page.changes:
        if ch.removed or not ch.blocking or ch.event_id in own:
            await conn.execute(
                "delete from crm_external_busy"
                " where calendar_connection_id = %s and external_event_id = %s",
                (c.id, ch.event_id),
            )
        elif ch.start and ch.end and ch.end > ch.start:
            await conn.execute(
                "insert into crm_external_busy"
                " (resource_id, calendar_connection_id, during, external_event_id)"
                " values (%s, %s, tstzrange(%s, %s), %s)"
                " on conflict (calendar_connection_id, external_event_id)"
                " do update set during = excluded.during",
                (c.resource_id, c.id, ch.start, ch.end, ch.event_id),
            )
    await conn.execute(
        "update crm_calendar_connection set sync_token = %s,"
        " window_end = case when %s then %s else window_end end,"
        " last_synced_at = now(), last_error = null, status = 'active' where id = %s",
        (page.delta_link, full, now + WINDOW, c.id),
    )


async def ensure_subscription(
    conn: AsyncConnection, settings: Settings, graph: ms.GraphClient, c: Connection
) -> None:
    """Keep a change-notification subscription alive, so Outlook changes arrive within
    seconds. Needs a public HTTPS API URL; without one the 5-minute sync covers it."""
    if not settings.public_api_url:
        return
    now = datetime.now(UTC)
    expires = now + SUBSCRIPTION_LIFE
    if c.subscription_id and c.subscription_expires_at:
        if c.subscription_expires_at - now > RENEW_WITHIN:
            return
        try:
            until = await graph.renew(c.subscription_id, expires)
            await conn.execute(
                "update crm_calendar_connection set subscription_expires_at = %s where id = %s",
                (until, c.id),
            )
            return
        except ms.GraphError as exc:
            if exc.status != 404:
                raise
    client_state = secrets.token_urlsafe(24)
    url = settings.public_api_url.rstrip("/") + f"/webhooks/{WEBHOOK_PROVIDER}"
    try:
        sub_id, until = await graph.subscribe(url, client_state, expires)
    except ms.GraphError as exc:
        # Microsoft couldn't reach the webhook: keep syncing on the timer.
        log.warning("calendar %s: subscription failed: %s", c.id, exc)
        return
    await conn.execute(
        "update crm_calendar_connection set subscription_id = %s, subscription_expires_at = %s,"
        " client_state = %s where id = %s",
        (sub_id, until, client_state, c.id),
    )


@crm_tasks.task(name="sync_all_calendars", pass_context=True, cron="*/5 * * * *")
async def sync_all_calendars(context: procrastinate.JobContext, timestamp: int) -> None:
    pool: AsyncConnectionPool = context.additional_context[POOL_KEY]
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select app.crm_defer_calendar_syncs()")


# --- Write: bookings -> Outlook events ------------------------------------------------


async def _subject(conn: AsyncConnection, appointment_id: UUID) -> tuple[str, str]:
    cur = await conn.execute(
        "select s.name_i18n, cu.display_name from crm_appointment a"
        " join crm_service s on s.id = a.service_id"
        " join customer cu on cu.id = a.customer_id where a.id = %s",
        (appointment_id,),
    )
    row = await cur.fetchone()
    names: dict[str, str] = row[0] if row else {}
    service = next(iter(names.values()), "Appointment")
    customer = (row[1] if row else None) or "Customer"
    return f"{service} – {customer}", f"{service} with {customer}.\nBooked via Confluo."


@tenant_task(crm_tasks, name="push_appointment", pass_deps=True)
async def push_appointment(
    conn: AsyncConnection,
    deps: JobDeps,
    appointment_id: str,
    deleted: bool = False,
    old_resource_id: str | None = None,
    old_event_id: str | None = None,
) -> None:
    settings = _settings(deps)
    graphs: dict[UUID, tuple[Connection, ms.GraphClient] | None] = {}

    async def graph_of(resource_id: UUID) -> tuple[Connection, ms.GraphClient] | None:
        if resource_id not in graphs:
            c = await load_connection(conn, resource_id=resource_id)
            graphs[resource_id] = (
                (c, await graph_for(deps.pool, settings, c)) if c and c.status == "active" else None
            )
        return graphs[resource_id]

    if deleted:
        if old_event_id and old_resource_id and (g := await graph_of(UUID(old_resource_id))):
            await g[1].delete_event(old_event_id)
        return

    appointment = UUID(appointment_id)
    cur = await conn.execute(
        "select resource_id, status, lower(during), upper(during), external_event_id"
        " from crm_appointment where id = %s",
        (appointment,),
    )
    row = await cur.fetchone()
    if row is None:
        return
    resource, status, start, end, event_id = row

    # Moved to someone else: the event leaves the previous person's calendar.
    if event_id and old_resource_id and UUID(old_resource_id) != resource:
        if g := await graph_of(UUID(old_resource_id)):
            await g[1].delete_event(event_id)
        event_id = None

    target = await graph_of(resource)
    if status == "confirmed" and target:
        c, graph = target
        subject, text = await _subject(conn, appointment)
        body = ms.event_body(
            appointment_id=appointment, subject=subject, text=text, start=start, end=end
        )
        if event_id:
            try:
                await graph.update_event(
                    event_id, {k: v for k, v in body.items() if k != "transactionId"}
                )
            except ms.GraphError as exc:
                if exc.status != 404:  # deleted in Outlook: create it again
                    raise
                event_id = None
        if not event_id:
            event_id = await graph.create_event(c.calendar_id, body)
        # A sync may have seen the event before we recorded it as ours.
        await conn.execute(
            "delete from crm_external_busy"
            " where calendar_connection_id = %s and external_event_id = %s",
            (c.id, event_id),
        )
    elif status in ("cancelled", "pending_approval") and event_id:
        if target:
            await target[1].delete_event(event_id)
        event_id = None
    # no_show / completed: the event stays as a record of what happened.

    await conn.execute(
        "update crm_appointment set external_event_id = %s"
        " where id = %s and external_event_id is distinct from %s",
        (event_id, appointment, event_id),
    )


def push_args(tenant_id: UUID, appointment_id: UUID) -> dict[str, Any]:
    return {"tenant_id": str(tenant_id), "appointment_id": str(appointment_id), "deleted": False}
