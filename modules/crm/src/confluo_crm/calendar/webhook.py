"""Microsoft Graph change notifications for connected Outlook calendars.

Graph POSTs to /webhooks/microsoft-calendar:
- once, when a subscription is created, with `?validationToken=…`: we echo it back
  (`handshake`), proving we own the URL;
- then `{"value": [{"subscriptionId", "clientState", "changeType", ...}]}` on every
  change. Notifications carry no signature: the per-subscription `clientState` (a
  random secret we chose) authenticates them, checked in `route`. A notification only
  says "something changed", so handling it just queues a delta sync.
"""

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from confluo_core.tenancy import tenant_transaction
from confluo_core.webhooks import WebhookEvent
from confluo_crm.calendar.sync import WEBHOOK_PROVIDER


class MicrosoftCalendarProvider:
    key = WEBHOOK_PROVIDER

    def handshake(self, query: Mapping[str, str]) -> str | None:
        token = query.get("validationToken")
        return token[:1024] if token else None

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True  # clientState is checked against the subscription in `route`

    def external_id(self, payload: dict[str, Any], headers: Mapping[str, str]) -> str:
        values = payload["value"]
        if not isinstance(values, list) or not values:
            raise KeyError("value")
        canonical = repr(sorted(repr(sorted(v.items())) for v in values))
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _connection(
        self, pool: AsyncConnectionPool, item: Mapping[str, Any]
    ) -> tuple[UUID, UUID] | None:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select tenant_id, connection_id, client_state"
                " from app.crm_calendar_by_subscription(%s)",
                (str(item.get("subscriptionId", "")),),
            )
            row = await cur.fetchone()
        if row is None or not row[2]:
            return None
        if not hmac.compare_digest(str(item.get("clientState", "")), row[2]):
            return None
        return row[0], row[1]

    async def route(self, event: WebhookEvent, pool: AsyncConnectionPool) -> UUID | None:
        found = await self._connection(pool, event.payload["value"][0])
        return found[0] if found else None

    async def handle(
        self,
        event: WebhookEvent,
        tenant_id: UUID,
        pool: AsyncConnectionPool,
        context: Mapping[str, Any],
    ) -> None:
        connections = set()
        for item in event.payload["value"]:
            found = await self._connection(pool, item)
            if found and found[0] == tenant_id:
                connections.add(found[1])
        async with tenant_transaction(pool, tenant_id) as conn:
            for connection_id in connections:
                await conn.execute(
                    "select app.defer_job('crm:sync_calendar', %s, %s)"
                    " where not exists (select from procrastinate.procrastinate_jobs"
                    "  where lock = %s and status = 'todo')",
                    (
                        Jsonb({"tenant_id": str(tenant_id), "connection_id": str(connection_id)}),
                        f"calsync:{connection_id}",
                        f"calsync:{connection_id}",
                    ),
                )
