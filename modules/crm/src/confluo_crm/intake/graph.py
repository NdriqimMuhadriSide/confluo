"""The intake graph (ARCHITECTURE.md §4): one run per inbound customer turn.

    load_context ─┬─ (human handles it / AI off) ─────────────────────────────▶ END
                  └─ understand ─┬─ faq ─────────────▶ answer_faq ─┬─▶ compose_reply
                                 ├─ greeting ──────────────────────┘        │
                                 ├─ booking ─────────▶ booking ──┐          ▼
                                 ├─ reschedule/cancel ▶ change ──┤      guardrails
                                 └─ other / human / unsure ──────┴▶ handoff ─▶ │
                                                                              ▼
                                                                            send ─▶ END

thread_id = conversation id with a Postgres checkpointer, so a conversation resumes
on any worker, days later, with its transcript and last intent. Every node runs
through @ai_node and lands in ai_action. The LLM interprets and phrases; code decides
routing. Booking runs the step-by-step flow in booking.py (offers only real free
times, books after an explicit yes); while a booking is in progress, short answers
("2", "Lotte", "yes") continue it. Rescheduling and cancelling still hand the
customer to staff.
"""

import operator
import re
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from confluo_core.ai_trace import AIRun, ai_node, current_trace
from confluo_core.llm import LLMGateway, Message, RefusedError
from confluo_core.tenancy import tenant_transaction
from confluo_crm.channels.base import ChannelAdapter, store_outbound
from confluo_crm.intake import booking as flow
from confluo_crm.intake import prompts
from confluo_crm.knowledge import hybrid_search

CONFIDENCE_FLOOR = 0.5
MAX_REPLY_CHARS = 700
TRANSCRIPT_TURNS = 12

Intent = Literal["booking", "reschedule", "cancel", "faq", "greeting", "human", "other"]
Language = Literal["en", "nl", "fr", "de", "sq"]
LANGS = ("en", "nl", "fr", "de", "sq")


class Entities(BaseModel):
    service: str | None = None
    date: str | None = None
    time: str | None = None


class Understanding(BaseModel):
    language: Language
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    entities: Entities
    summary: str


class Turn(TypedDict):
    role: Literal["customer", "assistant"]
    text: str


class IntakeState(TypedDict, total=False):
    # Per turn (input)
    inbound: str
    message_id: str
    language_hint: str | None
    # Carried across turns by the checkpointer
    transcript: Annotated[list[Turn], operator.add]
    language: str
    intent: str
    turns: int
    # Per turn (derived)
    skip: bool
    business: str
    understanding: dict[str, Any]
    kb: list[dict[str, Any]]
    kind: str  # what compose_reply should do: answer, greet, handoff, booking
    handoff_reason: str | None
    reply: str
    booking: dict[str, Any]  # carried across turns: the booking in progress
    draft: str  # booking: what to say, in English


def _messages(state: IntakeState) -> list[Message]:
    turns = state.get("transcript", [])[-TRANSCRIPT_TURNS:]
    return [Message("user" if t["role"] == "customer" else "assistant", t["text"]) for t in turns]


def build_graph(
    *,
    run: AIRun,
    llm: LLMGateway,
    adapter: ChannelAdapter,
    checkpointer: AsyncPostgresSaver,
) -> Any:
    tenant, conversation = run.tenant_id, run.conversation_id
    assert conversation is not None

    @ai_node("load_context")
    async def load_context(state: IntakeState, run: AIRun) -> IntakeState:
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            cur = await conn.execute(
                "select c.handler, c.language, t.name,"
                " coalesce((select (m.config->>'ai_enabled')::boolean from tenant_module m"
                "  where m.tenant_id = c.tenant_id and m.module_key = 'crm'), true)"
                " from crm_conversation c join tenant t on t.id = c.tenant_id where c.id = %s",
                (conversation,),
            )
            row = await cur.fetchone()
        assert row is not None
        handler, language, business, ai_enabled = row
        skip = handler != "ai" or not ai_enabled
        current_trace().rationale = (
            "a person handles this conversation"
            if handler != "ai"
            else ("AI answers are switched off" if not ai_enabled else None)
        )
        return {
            "skip": skip,
            "business": business,
            "language": state.get("language")
            or language
            or (state.get("language_hint") or "en")[:2],
            "turns": state.get("turns", 0) + 1,
            "kb": [],
            "handoff_reason": None,
            "draft": "",
        }

    @ai_node("understand")
    async def understand(state: IntakeState, run: AIRun) -> IntakeState:
        try:
            result = await llm.structured(
                tenant,
                "fast",
                "intake_understand",
                Understanding,
                system=prompts.understand(state["business"], state.get("language_hint")),
                messages=_messages(state),
                max_tokens=512,
                run_id=run.run_id,
            )
        except RefusedError:
            # The model declined to classify: let a person look at it.
            result = Understanding(
                language=state["language"] if state["language"] in LANGS else "en",
                intent="other",
                confidence=0.0,
                entities=Entities(),
                summary="model refused",
            )
        trace = current_trace()
        trace.rationale, trace.confidence = result.summary, result.confidence
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            await conn.execute(
                "update crm_conversation set language = %s where id = %s",
                (result.language, conversation),
            )
        return {
            "understanding": result.model_dump(),
            "language": result.language,
            "intent": result.intent,
        }

    def route(state: IntakeState) -> str:
        u = state["understanding"]
        if u["intent"] == "human":
            return "handoff"
        # Mid-booking, answers like "2", "Lotte" or "yes" continue the booking.
        if flow.is_active(state.get("booking")) and u["intent"] not in (
            "faq",
            "cancel",
            "reschedule",
        ):
            return "booking"
        if u["intent"] == "other" or u["confidence"] < CONFIDENCE_FLOOR:
            return "handoff"
        return {
            "faq": "answer_faq",
            "greeting": "answer_faq",
            "booking": "booking",
            "reschedule": "change",
            "cancel": "change",
        }[u["intent"]]

    @ai_node("answer_faq")
    async def answer_faq(state: IntakeState, run: AIRun) -> IntakeState:
        if state["understanding"]["intent"] == "greeting":
            return {"kind": "greet"}
        question = state["transcript"][-1]["text"]
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            hits = await hybrid_search(conn, llm, tenant, question, limit=5)
        trace = current_trace()
        trace.tool = "kb_search"
        trace.sources = [
            {"item_id": str(h.item_id), "title": h.title, "score": h.score} for h in hits
        ]
        if not hits:
            return {"kind": "handoff", "handoff_reason": "no_knowledge"}
        return {
            "kind": "answer",
            "kb": [
                {"title": h.title, "content": h.content, "item_id": str(h.item_id)} for h in hits
            ],
        }

    @ai_node("booking")
    async def booking(state: IntakeState, run: AIRun) -> IntakeState:
        current = dict(state.get("booking") or {})
        if not flow.is_active(current):
            current = {}
        language = state["language"]
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            ctx = await flow.load_context(conn, language, current.get("service_id"))
        if not ctx.services:
            current_trace().rationale = "no services set up"
            return {"kind": "handoff", "handoff_reason": "no_services", "booking": {}}
        update = await llm.structured(
            tenant,
            "fast",
            "intake_booking",
            flow.BookingUpdate,
            system=flow.prompt(state["business"], ctx, current),
            messages=_messages(state),
            max_tokens=512,
            run_id=run.run_id,
        )
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            if update.service and not current.get("service_id"):
                # The chosen service may have required fields: load them too.
                match = next(
                    (sid for sid, n in ctx.services if n.lower() == update.service.lower()), None
                )
                if match:
                    ctx = await flow.load_context(conn, language, str(match))
            outcome = await flow.advance(conn, ctx, current, update, conversation_id=conversation)
        trace = current_trace()
        trace.tool = "book_appointment" if outcome.appointment_id else "find_slots"
        trace.rationale = f"stage {outcome.state.get('stage')}: {update.model_dump_json()}"
        if outcome.handoff:
            return {
                "kind": "handoff",
                "handoff_reason": outcome.handoff,
                "booking": outcome.state,
                "draft": outcome.draft,
            }
        return {"kind": "booking", "booking": outcome.state, "draft": outcome.draft}

    @ai_node("change")
    async def change(state: IntakeState, run: AIRun) -> IntakeState:
        intent = state["understanding"]["intent"]
        current_trace().rationale = f"{intent} not automated yet: staff take over"
        return {"handoff_reason": intent}

    @ai_node("handoff")
    async def handoff(state: IntakeState, run: AIRun) -> IntakeState:
        reason = state.get("handoff_reason") or (
            "asked_for_human"
            if state["understanding"]["intent"] == "human"
            else "low_confidence"
            if state["understanding"]["confidence"] < CONFIDENCE_FLOOR
            else "other"
        )
        current_trace().rationale = reason
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            await conn.execute(
                "update crm_conversation set handler = 'human', status = 'waiting' where id = %s",
                (conversation,),
            )
        return {"kind": "handoff", "handoff_reason": reason}

    def after_faq(state: IntakeState) -> str:
        return "handoff" if state.get("kind") == "handoff" else "compose_reply"

    def after_booking(state: IntakeState) -> str:
        return "handoff" if state.get("kind") == "handoff" else "compose_reply"

    @ai_node("compose_reply")
    async def compose_reply(state: IntakeState, run: AIRun) -> IntakeState:
        result = await llm.chat(
            tenant,
            "dialogue",
            "intake_reply",
            system=prompts.reply(
                business=state["business"],
                language=state["language"],
                kind=state.get("kind", "answer"),
                knowledge=state.get("kb", []),
                handoff_reason=state.get("handoff_reason"),
                draft=state.get("draft", ""),
            ),
            messages=_messages(state),
            max_tokens=1024,
            run_id=run.run_id,
        )
        trace = current_trace()
        trace.sources = [
            {"item_id": k["item_id"], "title": k["title"]} for k in state.get("kb", [])
        ]
        if result.stop_reason == "refusal":
            return {"reply": "", "kind": "handoff", "handoff_reason": "refusal"}
        return {"reply": result.text}

    @ai_node("guardrails")
    async def guardrails(state: IntakeState, run: AIRun) -> IntakeState:
        reply = state.get("reply", "").strip()
        if not reply:
            reply = prompts.fallback(state["language"])
            current_trace().rationale = "empty reply replaced by fallback"
        if len(reply) > MAX_REPLY_CHARS:
            cut = reply[:MAX_REPLY_CHARS]
            ends = [m.end() for m in re.finditer(r"[.!?](\s|$)", cut)]
            reply = cut[: ends[-1]].strip() if ends else cut.rstrip() + "…"
            current_trace().rationale = "reply shortened for chat"
        return {"reply": reply}

    @ai_node("send")
    async def send(state: IntakeState, run: AIRun) -> IntakeState:
        async with tenant_transaction(run.pool, tenant, actor="ai") as conn:
            outbound = await store_outbound(conn, conversation, state["reply"])
            status = await adapter.send(conn, outbound)
            await conn.execute(
                "update crm_message set delivery_status = %s where id = %s",
                (status, outbound.message_id),
            )
        current_trace().tool = f"send:{adapter.channel}"
        return {"transcript": [{"role": "assistant", "text": state["reply"]}]}

    graph = StateGraph(IntakeState)
    nodes = {
        "load_context": load_context,
        "understand": understand,
        "answer_faq": answer_faq,
        "booking": booking,
        "change": change,
        "handoff": handoff,
        "compose_reply": compose_reply,
        "guardrails": guardrails,
        "send": send,
    }
    for name, fn in nodes.items():
        graph.add_node(name, _bind(fn, run))
    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "load_context", lambda s: END if s["skip"] else "understand", ["understand", END]
    )
    graph.add_conditional_edges("understand", route, ["answer_faq", "booking", "change", "handoff"])
    graph.add_conditional_edges("answer_faq", after_faq, ["compose_reply", "handoff"])
    graph.add_conditional_edges("booking", after_booking, ["compose_reply", "handoff"])
    graph.add_edge("change", "handoff")
    graph.add_edge("handoff", "compose_reply")
    graph.add_edge("compose_reply", "guardrails")
    graph.add_edge("guardrails", "send")
    graph.add_edge("send", END)
    return graph.compile(checkpointer=checkpointer)


def _bind(fn: Any, run: AIRun) -> Any:
    async def node(state: IntakeState) -> IntakeState:
        result: IntakeState = await fn(state, run)
        return result

    node.__name__ = fn.__name__
    return node


async def run_turn(
    *,
    run: AIRun,
    llm: LLMGateway,
    adapter: ChannelAdapter,
    checkpointer: AsyncPostgresSaver,
    text: str,
    message_id: UUID,
    language_hint: str | None,
) -> IntakeState:
    graph = build_graph(run=run, llm=llm, adapter=adapter, checkpointer=checkpointer)
    state: IntakeState = await graph.ainvoke(
        {
            "inbound": text,
            "message_id": str(message_id),
            "language_hint": language_hint,
            "transcript": [{"role": "customer", "text": text}],
        },
        config={"configurable": {"thread_id": str(run.conversation_id)}},
    )
    return state
