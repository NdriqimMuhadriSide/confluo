"""Webhook ingress (ARCHITECTURE.md §1, "Request lifecycle").

`POST /webhooks/{provider}` verifies the provider's signature, then in ONE transaction
records the event in the `inbound_event` ledger and enqueues `process_inbound_event`.
The ledger's unique (provider, external_id) makes redelivery a no-op: no second row,
no second job. The API answers 200 immediately; the worker does the work.

Providers implement `WebhookProvider`. Channel modules (WhatsApp, email, ...) add real
ones; the built-in `test` provider (HMAC-SHA256, local/test only) exercises the path.
"""

import hashlib
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import procrastinate
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from confluo_core.jobs import (
    POOL_KEY,
    core_tasks,
    is_last_attempt,
    record_failure,
)
from confluo_core.settings import Settings
from confluo_core.tenancy import tenant_transaction

log = logging.getLogger("confluo.webhooks")

PROVIDERS_KEY = "webhook_providers"


@dataclass(frozen=True)
class WebhookEvent:
    id: UUID
    provider: str
    external_id: str
    payload: dict[str, Any]
    headers: dict[str, str]
    attempts: int


class WebhookProvider(Protocol):
    key: str

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        """True if the request really comes from the provider (signature check)."""
        ...

    def external_id(self, payload: dict[str, Any], headers: Mapping[str, str]) -> str:
        """The provider's id for this delivery; redeliveries reuse it."""
        ...

    async def route(self, event: WebhookEvent, pool: AsyncConnectionPool) -> UUID | None:
        """The tenant this event belongs to (e.g. via crm_channel_connection)."""
        ...

    async def handle(
        self,
        event: WebhookEvent,
        tenant_id: UUID,
        pool: AsyncConnectionPool,
        context: Mapping[str, Any],
    ) -> None:
        """Process the event for its tenant (`context`: the worker's job context, e.g.
        the LLM gateway under LLM_KEY). Raise to retry."""
        ...


class UnroutableEvent(Exception):
    pass


class TestProvider:
    """HMAC-SHA256 signed test events: `X-Confluo-Signature: sha256=<hex>`.

    Payload: {"id": ..., "tenant_id": ..., "location": "<name>"} creates a location;
    {"fail": "<message>"} raises, to exercise retries.
    """

    key = "test"
    __test__ = False  # not a pytest test class

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def sign(self, body: bytes) -> str:
        return "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        given = headers.get("x-confluo-signature", "")
        return hmac.compare_digest(given, self.sign(body))

    def external_id(self, payload: dict[str, Any], headers: Mapping[str, str]) -> str:
        return str(payload["id"])

    async def route(self, event: WebhookEvent, pool: AsyncConnectionPool) -> UUID | None:
        tenant = event.payload.get("tenant_id")
        return UUID(tenant) if tenant else None

    async def handle(
        self,
        event: WebhookEvent,
        tenant_id: UUID,
        pool: AsyncConnectionPool,
        context: Mapping[str, Any],
    ) -> None:
        if "fail" in event.payload:
            raise RuntimeError(str(event.payload["fail"]))
        async with tenant_transaction(pool, tenant_id) as conn:
            await conn.execute(
                "insert into location (name) values (%s)", (event.payload["location"],)
            )


def default_providers(
    settings: Settings, modules: Mapping[str, Any] | None = None
) -> dict[str, WebhookProvider]:
    providers: dict[str, WebhookProvider] = {}
    for module in (modules or {}).values():
        for provider in module.webhook_providers():
            providers[provider.key] = provider
    if settings.webhook_test_secret and settings.env in ("local", "test"):
        test = TestProvider(settings.webhook_test_secret.get_secret_value())
        providers[test.key] = test
    return providers


async def _finish(
    pool: AsyncConnectionPool, event_id: UUID, status: str, tenant: UUID | None, error: str | None
) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "select app.finish_inbound_event(%s, %s, %s, %s)", (event_id, status, tenant, error)
        )


@core_tasks.task(name="process_inbound_event", pass_context=True)
async def process_inbound_event(context: procrastinate.JobContext, /, event_id: str) -> None:
    pool: AsyncConnectionPool = context.additional_context[POOL_KEY]
    providers: Mapping[str, WebhookProvider] = context.additional_context[PROVIDERS_KEY]
    eid = UUID(event_id)
    async with pool.connection() as conn, conn.transaction():
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute("select * from app.start_inbound_event(%s)", (eid,))
        row = await cur.fetchone()
    if row is None or row["id"] is None:
        log.error("inbound event %s not found", event_id)
        return
    event = WebhookEvent(
        id=row["id"],
        provider=row["provider"],
        external_id=row["external_id"],
        payload=row["payload"],
        headers=row["headers"],
        attempts=row["attempts"],
    )
    tenant: UUID | None = row["tenant_id"]
    try:
        provider = providers[event.provider]
        tenant = tenant or await provider.route(event, pool)
        if tenant is None:
            raise UnroutableEvent(f"no tenant for {event.provider} event {event.external_id}")
        await provider.handle(event, tenant, pool, context.additional_context)
    except Exception as exc:
        status = "dead" if is_last_attempt(context) else "failed"
        await _finish(pool, eid, status, tenant, f"{type(exc).__name__}: {exc}"[:2000])
        if tenant is not None:
            await record_failure(pool, context, tenant, exc)
        raise
    await _finish(pool, eid, "processed", tenant, None)
