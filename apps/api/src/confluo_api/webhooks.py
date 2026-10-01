"""Public webhook endpoint: verify, record once, enqueue, answer fast."""

import json
from collections.abc import Mapping

import procrastinate
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from confluo_core.deps import Pool
from confluo_core.jobs import defer_in
from confluo_core.webhooks import WebhookProvider, process_inbound_event

router = APIRouter()


class WebhookAck(BaseModel):
    status: str  # "accepted" | "duplicate"


@router.post(
    "/webhooks/{provider}",
    tags=["webhooks"],
    operation_id="receiveWebhook",
    response_model=WebhookAck,
)
async def receive(provider: str, request: Request, pool: Pool) -> WebhookAck | PlainTextResponse:
    providers: Mapping[str, WebhookProvider] = request.app.state.webhook_providers
    impl = providers.get(provider)
    if impl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")
    # Some providers first prove we own the URL (e.g. Microsoft Graph's validationToken).
    handshake = getattr(impl, "handshake", None)
    if handshake is not None:
        answer = handshake(request.query_params)
        if answer is not None:
            return PlainTextResponse(answer)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not impl.verify(headers, body):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    try:
        payload = json.loads(body)
        external_id = impl.external_id(payload, headers)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed payload") from None

    job_app: procrastinate.App = request.app.state.job_app
    task = job_app.tasks["core:process_inbound_event"]
    kept = {k: v for k, v in headers.items() if k.startswith(("x-", "content-type", "user-agent"))}
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "select app.record_inbound_event(%s, %s, %s, %s)",
            (provider, external_id, Jsonb(payload), Jsonb(kept)),
        )
        row = await cur.fetchone()
        event_id = row[0] if row else None
        if event_id is None:
            return WebhookAck(status="duplicate")
        await defer_in(conn, task, event_id=str(event_id))
    _ = process_inbound_event  # imported for task registration
    return WebhookAck(status="accepted")
