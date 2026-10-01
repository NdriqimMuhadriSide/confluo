"""Public endpoints of the web chat widget (no login; the widget key identifies the
business). Mounted under /public/crm.

    GET  /web/{key}/config            branding for the widget
    WS   /web/{key}/socket?session=…  chat: history on connect, then
         client → {"type": "message", "id": "<client id>", "text": "...", "lang": "nl"}
         server → {"type": "ack", "id": ...} | {"type": "message", "message": {...}}
                  | {"type": "error", "error": ...}

Checks: the key must belong to an active connection, the page's Origin must be in
the connection's allowed_origins (when set), the session must look like a random
token, and each session may send at most RATE_LIMIT messages per minute.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

import procrastinate
import psycopg
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from confluo_core.jobs import defer_in
from confluo_core.tenancy import tenant_transaction
from confluo_crm.channels.web import MAX_TEXT, NOTIFY_CHANNEL

log = logging.getLogger("confluo.webchat")
router = APIRouter(tags=["web-chat"])

SESSION = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RATE_LIMIT = 20  # messages per session per minute
HISTORY = 50


class WidgetConfig(BaseModel):
    color: str
    title: dict[str, str]
    greeting: dict[str, str]
    logo_url: str | None


async def _connection(
    pool: AsyncConnectionPool, key: str
) -> tuple[UUID, UUID, dict[str, Any]] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select tenant_id, connection_id, status, settings"
            " from app.crm_connection_by_key('web', %s)",
            (key,),
        )
        row = await cur.fetchone()
    if row is None or row[2] != "active":
        return None
    return row[0], row[1], row[3] or {}


def _config(settings: dict[str, Any]) -> WidgetConfig:
    return WidgetConfig(
        color=settings.get("color") or "#111827",
        title=settings.get("title") or {},
        greeting=settings.get("greeting") or {},
        logo_url=settings.get("logo_url"),
    )


@router.get("/web/{key}/config", operation_id="webChatConfig")
async def widget_config(key: str, request: Request, response: Response) -> WidgetConfig:
    # Fetched by the widget from the business's own website: any origin may read it
    # (it's public branding; the socket checks allowed_origins).
    response.headers["Access-Control-Allow-Origin"] = "*"
    found = await _connection(request.app.state.pool, key)
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Unknown widget",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    return _config(found[2])


class WebChatHub:
    """Forwards NOTIFY messages from the worker to the sockets of their session.
    One LISTEN connection per API process, started on first use."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, session: str) -> asyncio.Queue[dict[str, Any]]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues[session].add(queue)
        return queue

    def unsubscribe(self, session: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues[session].discard(queue)
        if not self._queues[session]:
            del self._queues[session]

    async def _listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._url, autocommit=True
                ) as conn:
                    await conn.execute(f"listen {NOTIFY_CHANNEL}")
                    async for note in conn.notifies():
                        payload = json.loads(note.payload)
                        for session in payload.get("sessions", []):
                            for queue in list(self._queues.get(session, ())):
                                queue.put_nowait(payload["message"])
            except (psycopg.Error, OSError) as exc:
                log.warning("web chat listener reconnecting: %s", exc)
                await asyncio.sleep(1)


def _hub(app: Any) -> WebChatHub:
    hub: WebChatHub | None = getattr(app.state, "web_chat_hub", None)
    if hub is None:
        hub = WebChatHub(str(app.state.settings.database_url))
        app.state.web_chat_hub = hub
    return hub


_recent: dict[str, deque[float]] = defaultdict(deque)


def _allowed(session: str) -> bool:
    now = time.monotonic()
    window = _recent[session]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        return False
    window.append(now)
    return True


async def _history(pool: AsyncConnectionPool, tenant: UUID, session: str) -> list[dict[str, Any]]:
    async with tenant_transaction(pool, tenant) as conn:
        cur = await conn.execute(
            "select m.id, case when m.direction = 'inbound' then 'customer' else m.sender_type end,"
            " m.body, m.created_at from crm_message m"
            " join crm_conversation c on c.id = m.conversation_id"
            " join customer_identity i on i.customer_id = c.customer_id"
            " where i.type = 'web_session' and i.value_normalized = %s and c.channel = 'web'"
            " order by m.created_at desc limit %s",
            (session, HISTORY),
        )
        rows = await cur.fetchall()
    return [
        {"id": str(r[0]), "from": r[1], "text": r[2], "at": r[3].isoformat()}
        for r in reversed(rows)
    ]


@router.websocket("/web/{key}/socket")
async def socket(websocket: WebSocket, key: str, session: str = "") -> None:
    app = websocket.app
    pool: AsyncConnectionPool = app.state.pool
    found = await _connection(pool, key)
    origin = websocket.headers.get("origin")
    if found is None or not SESSION.match(session):
        await websocket.close(code=4404)
        return
    tenant, _connection_id, settings = found
    allowed = settings.get("allowed_origins") or []
    if allowed and origin not in allowed:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub = _hub(app)
    queue = hub.subscribe(session)

    async def push() -> None:
        while True:
            message = await queue.get()
            await websocket.send_json({"type": "message", "message": message})

    pusher = asyncio.create_task(push())
    try:
        await websocket.send_json(
            {"type": "history", "messages": await _history(pool, tenant, session)}
        )
        job_app: procrastinate.App = app.state.job_app
        task = job_app.tasks["core:process_inbound_event"]
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "message":
                continue
            client_id, text = str(data.get("id", "")), str(data.get("text", "")).strip()
            if not CLIENT_ID.match(client_id) or not text or len(text) > MAX_TEXT:
                await websocket.send_json({"type": "error", "id": client_id, "error": "invalid"})
                continue
            if not _allowed(session):
                await websocket.send_json(
                    {"type": "error", "id": client_id, "error": "rate_limited"}
                )
                continue
            payload = {
                "key": key,
                "session": session,
                "id": client_id,
                "text": text,
                "lang": str(data.get("lang") or "")[:5],
            }
            async with pool.connection() as conn, conn.transaction():
                cur = await conn.execute(
                    "select app.record_inbound_event('web', %s, %s, %s)",
                    (f"{key}:{session}:{client_id}", Jsonb(payload), Jsonb({"origin": origin})),
                )
                row = await cur.fetchone()
                if row and row[0]:
                    # One session's messages are processed one at a time, in order.
                    await defer_in(conn, task, lock=f"web:{key}:{session}", event_id=str(row[0]))
            await websocket.send_json({"type": "ack", "id": client_id})
    except WebSocketDisconnect:
        pass
    finally:
        pusher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pusher
        hub.unsubscribe(session, queue)
