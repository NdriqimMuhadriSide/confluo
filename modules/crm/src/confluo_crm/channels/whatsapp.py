"""WhatsApp Business through Meta's Cloud API.

Inbound: Meta POSTs to /webhooks/whatsapp, signed with Confluo's app secret
(X-Hub-Signature-256). The phone number id in the payload routes it to the business
(crm_channel_connection.external_account_id); the message goes through the same
ledger → ingest → intake graph path as web chat. Delivery receipts ("delivered",
"read") update the outbound message's delivery_status.

Outbound: a text message from the business's number, with the access token stored
encrypted for that connection. Free-form replies are allowed within 24 hours of the
customer's last message (WhatsApp's session window); later messages will need
approved templates (a separate card).

Meta verifies the webhook URL once with a GET (`hub.challenge`), answered by
`handshake`.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx2
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from confluo_core.jobs import LLM_KEY
from confluo_core.llm import LLMGateway
from confluo_core.secrets import load_secret
from confluo_core.settings import Settings, get_settings
from confluo_core.tenancy import tenant_transaction
from confluo_core.webhooks import WebhookEvent
from confluo_crm.channels.base import (
    DeliveryStatus,
    Identity,
    InboundMessage,
    OutboundMessage,
    ingest,
)
from confluo_crm.channels.web import MAX_TEXT, _checkpointer

log = logging.getLogger("confluo.whatsapp")

SECRET_PURPOSE = "crm.channel.whatsapp"
STATUS_ORDER = ["sent", "delivered", "read"]

_settings: Settings | None = None
_transport: httpx2.AsyncBaseTransport | None = None


def configure(
    settings: Settings | None = None, transport: httpx2.AsyncBaseTransport | None = None
) -> None:
    """Override settings and the HTTP transport (tests)."""
    global _settings, _transport
    _settings, _transport = settings, transport


def settings() -> Settings:
    return _settings or get_settings()


def _value(payload: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = payload["entry"][0]["changes"][0]["value"]
    return value


def _text(message: Mapping[str, Any]) -> str:
    kind = message.get("type")
    if kind == "text":
        return str(message["text"]["body"])
    if kind == "button":
        return str(message["button"].get("text", ""))
    if kind == "interactive":
        reply = message["interactive"].get("button_reply") or message["interactive"].get(
            "list_reply", {}
        )
        return str(reply.get("title", ""))
    # Photos, voice notes, stickers…: staff see that something arrived.
    return f"[{kind or 'message'}]"


class WhatsAppAdapter:
    channel = "whatsapp"

    def normalize(self, payload: Mapping[str, Any], connection_id: UUID) -> InboundMessage:
        value = _value(payload)
        message = value["messages"][0]
        contact = (value.get("contacts") or [{}])[0]
        return InboundMessage(
            channel="whatsapp",
            connection_id=connection_id,
            external_id=str(message["id"]),
            sender=Identity(
                "whatsapp",
                str(message["from"]),  # wa_id: the number in international format, no "+"
                verified=True,
                display_name=(contact.get("profile") or {}).get("name"),
            ),
            text=_text(message)[:MAX_TEXT],
        )

    async def send(self, conn: AsyncConnection, message: OutboundMessage) -> DeliveryStatus:
        cur = await conn.execute(
            "select c.external_account_id, c.credentials_ref, i.value_normalized"
            " from crm_conversation v"
            " join crm_channel_connection c on c.id = v.channel_connection_id"
            " join customer_identity i on i.customer_id = v.customer_id and i.type = 'whatsapp'"
            " where v.id = %s order by i.created_at limit 1",
            (message.conversation_id,),
        )
        row = await cur.fetchone()
        if row is None or row[1] is None:
            log.warning("whatsapp: no number or token for conversation %s", message.conversation_id)
            return "failed"
        phone_number_id, credentials_ref, wa_id = row
        cfg = settings()
        token = (await load_secret(conn, cfg, UUID(credentials_ref)))["access_token"]
        async with httpx2.AsyncClient(transport=_transport, timeout=15) as http:
            res = await http.post(
                f"{cfg.whatsapp_graph_url.rstrip('/')}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": wa_id,
                    "type": "text",
                    "text": {"body": message.text, "preview_url": False},
                },
            )
        if res.status_code >= 400:
            log.warning("whatsapp send failed: %s %s", res.status_code, res.text[:300])
            return "failed"
        wamid = res.json()["messages"][0]["id"]
        # Delivery receipts refer to this id.
        await conn.execute(
            "update crm_message set external_id = %s where id = %s", (wamid, message.message_id)
        )
        return "sent"


class WhatsAppProvider:
    key = "whatsapp"

    def __init__(self) -> None:
        self.adapter = WhatsAppAdapter()

    def handshake(self, query: Mapping[str, str]) -> str | None:
        expected = settings().whatsapp_verify_token
        if (
            query.get("hub.mode") == "subscribe"
            and expected
            and hmac.compare_digest(query.get("hub.verify_token", ""), expected)
        ):
            return query.get("hub.challenge", "")
        return None

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = settings().whatsapp_app_secret
        if secret is None:
            return False
        digest = hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(headers.get("x-hub-signature-256", ""), f"sha256={digest}")

    def external_id(self, payload: dict[str, Any], headers: Mapping[str, str]) -> str:
        value = _value(payload)
        if value.get("messages"):
            return str(value["messages"][0]["id"])
        if value.get("statuses"):
            status = value["statuses"][0]
            return f"status:{status['id']}:{status['status']}"
        return "other:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def route(self, event: WebhookEvent, pool: AsyncConnectionPool) -> UUID | None:
        phone_number_id = str(_value(event.payload).get("metadata", {}).get("phone_number_id"))
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select tenant_id, status from app.crm_connection_by_key('whatsapp', %s)",
                (phone_number_id,),
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
        value = _value(event.payload)
        phone_number_id = str(value["metadata"]["phone_number_id"])
        if value.get("statuses"):
            await self._statuses(pool, tenant_id, value["statuses"])
        if not value.get("messages"):
            return
        async with tenant_transaction(pool, tenant_id) as conn:
            cur = await conn.execute(
                "select id from crm_channel_connection"
                " where channel = 'whatsapp' and external_account_id = %s",
                (phone_number_id,),
            )
            row = await cur.fetchone()
            assert row is not None
            inbound = self.adapter.normalize(event.payload, row[0])
            result = await ingest(conn, inbound)
            if result.duplicate:
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
        from confluo_core.ai_trace import AIRun
        from confluo_crm.intake.graph import run_turn

        llm: LLMGateway = context[LLM_KEY]
        await run_turn(
            run=AIRun(pool, tenant_id, conversation_id=result.conversation_id),
            llm=llm,
            adapter=self.adapter,
            checkpointer=await _checkpointer(context),
            text=inbound.text,
            message_id=result.message_id,
            language_hint=None,
        )

    async def _statuses(
        self, pool: AsyncConnectionPool, tenant_id: UUID, statuses: list[dict[str, Any]]
    ) -> None:
        async with tenant_transaction(pool, tenant_id) as conn:
            for status in statuses:
                new = status.get("status")
                if new == "failed":
                    await conn.execute(
                        "update crm_message set delivery_status = 'failed' where external_id = %s",
                        (status["id"],),
                    )
                elif new in STATUS_ORDER:
                    # Receipts can arrive out of order: never go back from read to sent.
                    await conn.execute(
                        "update crm_message set delivery_status = %s where external_id = %s"
                        " and coalesce(array_position(%s::text[], delivery_status), 0)"
                        "  < array_position(%s::text[], %s)",
                        (new, status["id"], STATUS_ORDER, STATUS_ORDER, new),
                    )
