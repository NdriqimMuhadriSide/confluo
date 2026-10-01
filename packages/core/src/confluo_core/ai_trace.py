"""Record every agent step in `ai_action` (powers "why did the AI do this?").

    run = AIRun(pool, tenant_id, conversation_id=conv_id)

    @ai_node("understand")
    async def understand(state: dict, run: AIRun) -> dict:
        trace = current_trace()
        trace.rationale = "customer asks for a haircut on Friday"
        trace.confidence = 0.92
        return {"intent": "booking"}

The decorator times the call, captures input and output (truncated), the outcome
and anything the node or the LLM gateway put on the current trace (model, tokens,
sources, tool, rationale, confidence), and writes one row in its own transaction so
the trace survives even if the node's work rolls back. Errors are recorded and
re-raised.
"""

import functools
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from confluo_core.tenancy import tenant_transaction

MAX_JSON_CHARS = 16_000

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@dataclass
class AIRun:
    """One agent run (one inbound customer turn)."""

    pool: AsyncConnectionPool
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class NodeTrace:
    """Filled in during a node; the LLM gateway adds model and token usage."""

    tool: str | None = None
    rationale: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    approval_required: bool = False

    def add_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.model = model
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


_trace: ContextVar[NodeTrace | None] = ContextVar("ai_node_trace", default=None)


def current_trace() -> NodeTrace:
    """The trace of the node being run. Outside a node, a throwaway trace."""
    return _trace.get() or NodeTrace()


def _jsonable(value: Any) -> Any:
    """JSON-safe and size-capped copy for the log."""
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) > MAX_JSON_CHARS:
        return {"truncated": True, "preview": text[:MAX_JSON_CHARS]}
    return json.loads(text)


def ai_node(name: str) -> Callable[[F], F]:
    """Decorate an async agent node `node(state, run, ...)` to log it to ai_action."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(state: Any, run: AIRun, *args: Any, **kwargs: Any) -> Any:
            trace = NodeTrace()
            token = _trace.set(trace)
            started = time.perf_counter()
            output: Any = None
            outcome = "ok"
            try:
                result = await fn(state, run, *args, **kwargs)
                output = result
                return result
            except Exception as exc:
                outcome = "error"
                output = {"error": type(exc).__name__, "message": str(exc)}
                raise
            finally:
                _trace.reset(token)
                await _record(
                    run,
                    name,
                    trace,
                    state,
                    output,
                    outcome,
                    int((time.perf_counter() - started) * 1000),
                )

        return cast(F, wrapper)

    return decorate


async def _record(
    run: AIRun,
    node: str,
    trace: NodeTrace,
    state: Any,
    output: Any,
    outcome: str,
    latency_ms: int,
) -> None:
    async with tenant_transaction(run.pool, run.tenant_id, actor="ai") as conn:
        await conn.execute(
            "insert into ai_action (run_id, conversation_id, node, tool, input, output, rationale,"
            " sources, model, input_tokens, output_tokens, latency_ms, confidence, outcome,"
            " approval_status)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run.run_id,
                run.conversation_id,
                node,
                trace.tool,
                Jsonb(_jsonable(state if isinstance(state, Mapping) else {"state": state})),
                Jsonb(_jsonable(output if isinstance(output, Mapping) else {"result": output})),
                trace.rationale,
                Jsonb(_jsonable(trace.sources)),
                trace.model,
                trace.input_tokens or None,
                trace.output_tokens or None,
                latency_ms,
                trace.confidence,
                outcome,
                "pending" if trace.approval_required else "not_required",
            ),
        )
