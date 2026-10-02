"""The staff inbox. Mounted under /api/crm.

GET  /conversations                       newest first, with the last message
GET  /conversations/{id}                  the conversation and its messages
POST /conversations/{id}/messages         staff reply (takes over from the AI)
POST /conversations/{id}/handback         let the AI answer again
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import AsyncConnection
from pydantic import BaseModel, StringConstraints

from confluo_core.deps import TenantContext, requires
from confluo_crm.channels.base import store_outbound
from confluo_crm.channels.registry import adapter_for

router = APIRouter(tags=["crm-inbox"])

View = Annotated[TenantContext, Depends(requires("crm.inbox.view"))]
TakeOver = Annotated[TenantContext, Depends(requires("crm.inbox.takeover"))]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ConversationOut(BaseModel):
    id: UUID
    customer_id: UUID | None
    customer_name: str | None
    channel: str
    status: Literal["open", "waiting", "closed"]
    handler: Literal["ai", "human"]
    language: str | None
    last_message_at: datetime | None
    last_message: str | None
    appointments: int


class MessageOut(BaseModel):
    id: UUID
    direction: Literal["inbound", "outbound"]
    sender_type: Literal["customer", "ai", "staff", "system"]
    body: str | None
    delivery_status: str | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    text: Text


SELECT = (
    "select c.id, c.customer_id, cu.display_name, c.channel, c.status, c.handler, c.language,"
    " c.last_message_at,"
    " (select m.body from crm_message m where m.conversation_id = c.id"
    "  order by m.created_at desc limit 1),"
    " (select count(*) from crm_appointment a where a.conversation_id = c.id)::int"
    " from crm_conversation c left join customer cu on cu.id = c.customer_id"
)


def _conv(r: tuple[object, ...]) -> ConversationOut:
    return ConversationOut.model_validate(dict(zip(ConversationOut.model_fields, r, strict=True)))


async def _detail(conn: AsyncConnection, conversation_id: UUID) -> ConversationDetail:
    cur = await conn.execute(f"{SELECT} where c.id = %s", (conversation_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such conversation")
    cur = await conn.execute(
        "select id, direction, sender_type, body, delivery_status, created_at from crm_message"
        " where conversation_id = %s order by created_at",
        (conversation_id,),
    )
    messages = [
        MessageOut.model_validate(dict(zip(MessageOut.model_fields, m, strict=True)))
        for m in await cur.fetchall()
    ]
    return ConversationDetail(**_conv(row).model_dump(), messages=messages)


@router.get("/conversations", operation_id="listConversations")
async def list_conversations(
    tenant: View, limit: int = Query(50, ge=1, le=200)
) -> list[ConversationOut]:
    cur = await tenant.conn.execute(
        f"{SELECT} order by c.last_message_at desc nulls last limit %s", (limit,)
    )
    return [_conv(r) for r in await cur.fetchall()]


@router.get("/conversations/{conversation_id}", operation_id="getConversation")
async def get_conversation(conversation_id: UUID, tenant: View) -> ConversationDetail:
    return await _detail(tenant.conn, conversation_id)


@router.post("/conversations/{conversation_id}/messages", operation_id="replyToConversation")
async def reply(conversation_id: UUID, body: ReplyIn, tenant: TakeOver) -> ConversationDetail:
    conn = tenant.conn
    current = await _detail(conn, conversation_id)
    adapter = adapter_for(current.channel)
    if adapter is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Can't send on {current.channel} yet")
    # A person answering takes the conversation over from the AI.
    await conn.execute(
        "update crm_conversation set handler = 'human', status = 'open' where id = %s",
        (conversation_id,),
    )
    outbound = await store_outbound(conn, conversation_id, body.text, sender_type="staff")
    await conn.execute(
        "update crm_message set sender_member_id = %s where id = %s",
        (tenant.user.id, outbound.message_id),
    )
    delivery = await adapter.send(conn, outbound)
    await conn.execute(
        "update crm_message set delivery_status = %s where id = %s",
        (delivery, outbound.message_id),
    )
    return await _detail(conn, conversation_id)


@router.post("/conversations/{conversation_id}/handback", operation_id="handBackToAi")
async def hand_back(conversation_id: UUID, tenant: TakeOver) -> ConversationDetail:
    await _detail(tenant.conn, conversation_id)
    await tenant.conn.execute(
        "update crm_conversation set handler = 'ai', status = 'open' where id = %s",
        (conversation_id,),
    )
    return await _detail(tenant.conn, conversation_id)
