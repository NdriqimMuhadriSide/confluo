"""Intake graph: intent routing, ai_action per node, resuming from the checkpoint."""

import uuid
from typing import Any

import psycopg
import pytest

from confluo_core.ai_trace import AIRun
from confluo_core.tenancy import tenant_transaction
from confluo_crm.channels.base import (
    DeliveryStatus,
    Identity,
    InboundMessage,
    OutboundMessage,
    ingest,
)
from confluo_crm.intake.graph import run_turn
from tests.tenancy.conftest import World, make_checkpointer


class RecordingAdapter:
    channel = "test"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def normalize(self, payload: dict[str, object], connection_id: uuid.UUID) -> InboundMessage:
        raise NotImplementedError

    async def send(self, conn: Any, message: OutboundMessage) -> DeliveryStatus:
        self.sent.append(message)
        return "sent"


async def _connection(world: World, tenant: uuid.UUID) -> uuid.UUID:
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        row = conn.execute(
            "insert into crm_channel_connection (tenant_id, channel, external_account_id)"
            " values (%s, 'web', %s) returning id",
            (tenant, f"wk_{uuid.uuid4().hex}"),
        ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def say(
    worker: Any,
    world: World,
    shop: dict[str, uuid.UUID],
    connection: uuid.UUID,
    session: str,
    text: str,
    adapter: RecordingAdapter,
    checkpointer: Any = None,
) -> dict[str, Any]:
    tenant = shop["tenant"]
    async with tenant_transaction(worker.db, tenant) as conn:
        result = await ingest(
            conn,
            InboundMessage(
                "web",
                connection,
                f"{session}:{uuid.uuid4().hex[:8]}",
                Identity("web_session", session, verified=True),
                text,
            ),
        )
    run = AIRun(worker.db, tenant, conversation_id=result.conversation_id)
    state = await run_turn(
        run=run,
        llm=worker.gateway,
        adapter=adapter,
        checkpointer=checkpointer or worker.checkpointer,
        text=text,
        message_id=result.message_id,
        language_hint=None,
    )
    return {"state": state, "run": run, "conversation": result.conversation_id}


def _nodes(world: World, run: AIRun) -> list[str]:
    with psycopg.connect(world.owner_url) as conn:
        return [
            r[0]
            for r in conn.execute(
                "select node from ai_action where run_id = %s order by created_at", (run.run_id,)
            ).fetchall()
        ]


@pytest.mark.parametrize(
    ("text", "intent", "path"),
    [
        ("Hallo!", "greeting", ["answer_faq", "compose_reply"]),
        (
            "I want to book an appointment for friday",
            "booking",
            ["booking", "handoff", "compose_reply"],
        ),
        ("Can I reschedule my appointment?", "reschedule", ["change", "handoff", "compose_reply"]),
        ("Ik wil mijn afspraak annuleren", "cancel", ["change", "handoff", "compose_reply"]),
        ("Mag ik iemand spreken, een medewerker?", "human", ["handoff", "compose_reply"]),
        ("blue", "other", ["handoff", "compose_reply"]),
    ],
)
async def test_each_intent_takes_its_branch_and_every_node_is_logged(
    chat_worker: Any,
    world: World,
    chat_shop: dict[str, uuid.UUID],
    text: str,
    intent: str,
    path: list[str],
) -> None:
    worker, shop = chat_worker, chat_shop
    connection = await _connection(world, shop["tenant"])
    adapter = RecordingAdapter()
    out = await say(worker, world, shop, connection, uuid.uuid4().hex, text, adapter)
    assert out["state"]["intent"] == intent
    assert _nodes(world, out["run"]) == ["load_context", "understand", *path, "guardrails", "send"]
    [sent] = adapter.sent
    assert sent.text.strip()
    with psycopg.connect(world.owner_url) as conn:
        handler = conn.execute(
            "select handler from crm_conversation where id = %s", (out["conversation"],)
        ).fetchone()
    assert handler == (("ai",) if intent == "greeting" else ("human",))


async def test_faq_answers_from_the_knowledge_base_with_sources(
    chat_worker: Any, world: World, chat_shop: dict[str, uuid.UUID]
) -> None:
    worker, shop = chat_worker, chat_shop
    tenant = shop["tenant"]
    async with tenant_transaction(worker.db, tenant) as conn:
        cur = await conn.execute(
            "insert into crm_knowledge_item (kind, title, body, language, published)"
            " values ('faq', 'Parking', 'Free parking behind the salon.', 'en', true) returning id"
        )
        row = await cur.fetchone()
    assert row is not None
    connection = await _connection(world, tenant)
    adapter = RecordingAdapter()
    # Index the item through the real job function.
    from confluo_core.job_app import build_job_app
    from confluo_core.modules import discover_modules

    app = build_job_app(discover_modules())
    async with app.open_async(worker.db):
        await app.tasks["crm:embed_knowledge_item"].defer_async(
            tenant_id=str(tenant), item_id=str(row[0])
        )
    await worker.run()

    out = await say(
        worker, world, shop, connection, uuid.uuid4().hex, "Is there free parking?", adapter
    )
    assert out["state"]["intent"] == "faq"
    assert adapter.sent[0].text == "Free parking behind the salon."
    with psycopg.connect(world.owner_url) as conn:
        sources = conn.execute(
            "select sources from ai_action where run_id = %s and node = 'answer_faq'",
            (out["run"].run_id,),
        ).fetchone()
    assert sources is not None and sources[0][0]["title"] == "Parking"


async def test_question_without_knowledge_hands_off(
    chat_worker: Any, world: World, chat_shop: dict[str, uuid.UUID]
) -> None:
    worker, shop = chat_worker, chat_shop
    connection = await _connection(world, shop["tenant"])
    adapter = RecordingAdapter()
    out = await say(
        worker, world, shop, connection, uuid.uuid4().hex, "What is the price of a colour?", adapter
    )
    assert out["state"]["intent"] == "faq"
    assert out["state"]["handoff_reason"] == "no_knowledge"
    assert _nodes(world, out["run"])[2:4] == ["answer_faq", "handoff"]


async def test_human_handled_conversation_gets_no_ai_reply(
    chat_worker: Any, world: World, chat_shop: dict[str, uuid.UUID]
) -> None:
    worker, shop = chat_worker, chat_shop
    connection = await _connection(world, shop["tenant"])
    adapter = RecordingAdapter()
    session = uuid.uuid4().hex
    await say(worker, world, shop, connection, session, "I want a human please", adapter)
    out = await say(worker, world, shop, connection, session, "Hello?", adapter)
    assert len(adapter.sent) == 1  # only the handoff message
    assert _nodes(world, out["run"]) == ["load_context"]


async def test_conversation_resumes_from_the_checkpoint(
    chat_worker: Any, world: World, chat_shop: dict[str, uuid.UUID]
) -> None:
    worker, shop = chat_worker, chat_shop
    """A later turn on a brand-new checkpointer (another worker, days later) sees the
    earlier transcript and state."""
    connection = await _connection(world, shop["tenant"])
    adapter = RecordingAdapter()
    session = uuid.uuid4().hex
    first = await say(worker, world, shop, connection, session, "Bonjour !", adapter)
    assert first["state"]["language"] == "fr" and first["state"]["turns"] == 1

    other_saver, other_pool = await make_checkpointer(world)
    try:
        second = await say(
            worker, world, shop, connection, session, "Merci beaucoup", adapter, other_saver
        )
    finally:
        await other_pool.close()
    assert second["conversation"] == first["conversation"]
    state = second["state"]
    assert state["turns"] == 2
    assert [t["role"] for t in state["transcript"]] == [
        "customer",
        "assistant",
        "customer",
        "assistant",
    ]
    assert state["transcript"][0]["text"] == "Bonjour !"
