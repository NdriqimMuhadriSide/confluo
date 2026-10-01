"""The channel adapter contract: how a channel (web chat, WhatsApp, email, phone,
Instagram, ...) plugs into Confluo. See docs/CHANNELS.md for the full guide.

Inbound, every channel goes through the webhook ledger (idempotent, retried):

    provider.route(event)      -> tenant              (via crm_channel_connection)
    adapter.normalize(event)   -> InboundMessage      (channel-specific parsing)
    ingest(conn, inbound)      -> IngestResult        (shared: customer, conversation, message)
    -> crm:run_intake job                             (the AI, if the conversation is AI-handled)

Outbound, the intake graph stores the reply as a crm_message and calls
`adapter.send`, which delivers it and reports a delivery status.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from psycopg import AsyncConnection

IdentityType = Literal["phone", "email", "whatsapp", "instagram", "facebook", "web_session"]
DeliveryStatus = Literal["queued", "sent", "delivered", "read", "failed"]


@dataclass(frozen=True)
class Identity:
    type: IdentityType
    value: str  # normalized: E.164 phone, lower-case email, session token, ...
    verified: bool  # True when the channel proves ownership (a WhatsApp number, a session)
    display_name: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    connection_id: UUID
    external_id: str  # the provider's message id; unique per connection
    sender: Identity
    text: str
    language_hint: str | None = None  # e.g. the browser language


@dataclass(frozen=True)
class OutboundMessage:
    message_id: UUID
    conversation_id: UUID
    connection_id: UUID | None
    text: str


@dataclass(frozen=True)
class IngestResult:
    conversation_id: UUID
    message_id: UUID
    customer_id: UUID
    new_customer: bool
    handler: str  # "ai" or "human"
    duplicate: bool  # the message was already stored (redelivery)


class ChannelAdapter(Protocol):
    channel: str

    def normalize(self, payload: dict[str, object], connection_id: UUID) -> InboundMessage:
        """Turn the provider payload into an InboundMessage."""
        ...

    async def send(self, conn: AsyncConnection, message: OutboundMessage) -> DeliveryStatus:
        """Deliver a stored outbound message; return its delivery status."""
        ...


async def _customer_for(conn: AsyncConnection, sender: Identity) -> tuple[UUID, bool]:
    """Find the customer by identity, or create one. Only verified identities are
    unique per tenant, so an unverified match never merges two people."""
    cur = await conn.execute(
        "select customer_id from customer_identity"
        " where type = %s and value_normalized = %s and verified"
        " order by created_at limit 1",
        (sender.type, sender.value),
    )
    row = await cur.fetchone()
    if row:
        return row[0], False
    cur = await conn.execute(
        "insert into customer (display_name) values (%s) returning id", (sender.display_name,)
    )
    created = await cur.fetchone()
    assert created is not None
    await conn.execute(
        "insert into customer_identity"
        " (customer_id, type, value_normalized, verified, source_channel)"
        " values (%s, %s, %s, %s, %s)",
        (created[0], sender.type, sender.value, sender.verified, sender.type),
    )
    return created[0], True


async def ingest(conn: AsyncConnection, inbound: InboundMessage) -> IngestResult:
    """Store an inbound message for the transaction's tenant: resolve the customer,
    continue their open conversation on this connection (or open one), add the
    message. Idempotent on (connection, external_id)."""
    cur = await conn.execute(
        "select m.id, m.conversation_id, c.customer_id, c.handler from crm_message m"
        " join crm_conversation c on c.id = m.conversation_id"
        " where m.channel_connection_id = %s and m.external_id = %s",
        (inbound.connection_id, inbound.external_id),
    )
    existing = await cur.fetchone()
    if existing:
        return IngestResult(existing[1], existing[0], existing[2], False, existing[3], True)

    customer_id, new_customer = await _customer_for(conn, inbound.sender)
    cur = await conn.execute(
        "select id, handler from crm_conversation"
        " where customer_id = %s and channel_connection_id = %s and status <> 'closed'"
        " order by last_message_at desc nulls last limit 1",
        (customer_id, inbound.connection_id),
    )
    conv = await cur.fetchone()
    if conv is None:
        cur = await conn.execute(
            "insert into crm_conversation (customer_id, channel, channel_connection_id, language)"
            " values (%s, %s, %s, %s) returning id, handler",
            (customer_id, inbound.channel, inbound.connection_id, inbound.language_hint),
        )
        conv = await cur.fetchone()
        assert conv is not None
    cur = await conn.execute(
        "insert into crm_message (conversation_id, channel_connection_id, direction, sender_type,"
        " body, external_id) values (%s, %s, 'inbound', 'customer', %s, %s) returning id",
        (conv[0], inbound.connection_id, inbound.text, inbound.external_id),
    )
    msg = await cur.fetchone()
    assert msg is not None
    await conn.execute(
        "update crm_conversation set last_message_at = now(), status = 'open' where id = %s",
        (conv[0],),
    )
    return IngestResult(conv[0], msg[0], customer_id, new_customer, conv[1], False)


async def store_outbound(
    conn: AsyncConnection, conversation_id: UUID, text: str, sender_type: str = "ai"
) -> OutboundMessage:
    cur = await conn.execute(
        "select channel_connection_id from crm_conversation where id = %s", (conversation_id,)
    )
    row = await cur.fetchone()
    connection_id = row[0] if row else None
    cur = await conn.execute(
        "insert into crm_message (conversation_id, channel_connection_id, direction, sender_type,"
        " body, delivery_status) values (%s, %s, 'outbound', %s, %s, 'queued') returning id",
        (conversation_id, connection_id, sender_type, text),
    )
    msg = await cur.fetchone()
    assert msg is not None
    await conn.execute(
        "update crm_conversation set last_message_at = now() where id = %s", (conversation_id,)
    )
    return OutboundMessage(msg[0], conversation_id, connection_id, text)
