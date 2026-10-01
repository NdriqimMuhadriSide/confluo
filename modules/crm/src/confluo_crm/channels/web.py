"""Web chat channel: the embeddable widget (apps/widget) talks to the public
WebSocket in web_public.py, which records each customer message in the webhook
ledger; this provider routes it to the tenant, ingests it and runs the intake graph.
Replies reach the browser through Postgres NOTIFY on CHANNEL, addressed by session.
"""

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from confluo_core.ai_trace import AIRun
from confluo_core.jobs import LLM_KEY
from confluo_core.llm import LLMGateway
from confluo_core.settings import get_settings
from confluo_core.tenancy import tenant_transaction
from confluo_core.webhooks import WebhookEvent
from confluo_crm.channels.base import (
    DeliveryStatus,
    Identity,
    InboundMessage,
    OutboundMessage,
    ingest,
)

NOTIFY_CHANNEL = "crm_web_outbound"
CHECKPOINTER_KEY = "crm_checkpointer"  # optional in the job context (tests inject one)
MAX_TEXT = 2000


class WebChatAdapter:
    channel = "web"

    def normalize(self, payload: Mapping[str, Any], connection_id: UUID) -> InboundMessage:
        return InboundMessage(
            channel="web",
            connection_id=connection_id,
            external_id=f"{payload['session']}:{payload['id']}",
            sender=Identity("web_session", str(payload["session"]), verified=True),
            text=str(payload["text"])[:MAX_TEXT],
            language_hint=(str(payload.get("lang") or "")[:2] or None),
        )

    async def send(self, conn: AsyncConnection, message: OutboundMessage) -> DeliveryStatus:
        cur = await conn.execute(
            "select i.value_normalized from crm_conversation c"
            " join customer_identity i on i.customer_id = c.customer_id and i.type = 'web_session'"
            " where c.id = %s",
            (message.conversation_id,),
        )
        sessions = [r[0] for r in await cur.fetchall()]
        if not sessions:
            return "failed"
        cur = await conn.execute(
            "select sender_type, created_at from crm_message where id = %s", (message.message_id,)
        )
        row = await cur.fetchone()
        payload = {
            "sessions": sessions,
            "connection_id": str(message.connection_id),
            "message": {
                "id": str(message.message_id),
                "from": row[0] if row else "ai",
                "text": message.text,
                "at": row[1].isoformat() if row else None,
            },
        }
        # Delivered on commit; the API's WebChatHub forwards it to open sockets.
        await conn.execute("select pg_notify(%s, %s)", (NOTIFY_CHANNEL, json.dumps(payload)))
        return "sent"


_checkpointers: dict[str, AsyncPostgresSaver] = {}


async def _checkpointer(context: Mapping[str, Any]) -> AsyncPostgresSaver:
    if CHECKPOINTER_KEY in context:
        saver: AsyncPostgresSaver = context[CHECKPOINTER_KEY]
        return saver
    url = str(get_settings().database_url)
    if url not in _checkpointers:
        # LangGraph needs dict rows, autocommit and no prepared statements.
        pool = AsyncConnectionPool(
            url,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open()
        _checkpointers[url] = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    return _checkpointers[url]


class WebChatProvider:
    """Webhook-ledger provider for web chat. Events are recorded by our own socket
    endpoint, never posted from outside, so `verify` refuses every HTTP delivery."""

    key = "web"

    def __init__(self) -> None:
        self.adapter = WebChatAdapter()

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        return False

    def external_id(self, payload: dict[str, Any], headers: Mapping[str, str]) -> str:
        return f"{payload['key']}:{payload['session']}:{payload['id']}"

    async def route(self, event: WebhookEvent, pool: AsyncConnectionPool) -> UUID | None:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select tenant_id, status from app.crm_connection_by_key('web', %s)",
                (event.payload["key"],),
            )
            row = await cur.fetchone()
        return row[0] if row and row[1] == "active" else None

    async def handle(
        self,
        event: WebhookEvent,
        tenant_id: UUID,
        pool: AsyncConnectionPool,
        context: Mapping[str, Any],
    ) -> None:
        async with tenant_transaction(pool, tenant_id) as conn:
            cur = await conn.execute(
                "select id from crm_channel_connection"
                " where channel = 'web' and external_account_id = %s",
                (event.payload["key"],),
            )
            row = await cur.fetchone()
            assert row is not None
            inbound = self.adapter.normalize(event.payload, row[0])
            result = await ingest(conn, inbound)
            if result.duplicate:
                # A retry: only run the AI if the customer hasn't been answered yet.
                cur = await conn.execute(
                    "select direction from crm_message where conversation_id = %s"
                    " order by created_at desc limit 1",
                    (result.conversation_id,),
                )
                last = await cur.fetchone()
                if last and last[0] == "outbound":
                    return
        if result.handler != "ai":
            return
        from confluo_crm.intake.graph import run_turn

        llm: LLMGateway = context[LLM_KEY]
        await run_turn(
            run=AIRun(pool, tenant_id, conversation_id=result.conversation_id),
            llm=llm,
            adapter=self.adapter,
            checkpointer=await _checkpointer(context),
            text=inbound.text,
            message_id=result.message_id,
            language_hint=inbound.language_hint,
        )
