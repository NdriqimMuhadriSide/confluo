"""LLMGateway: the only way Confluo code talks to language and embedding models.

Callers ask for a *tier* ("fast" for intent/extraction, "dialogue" for replies,
"embedding"), never a vendor model. Settings map each tier to "provider:model"
(CONFLUO_LLM_FAST, CONFLUO_LLM_DIALOGUE, CONFLUO_LLM_EMBEDDING), so switching
provider or model is a config change. Every call is logged per tenant in
`llm_usage` (tokens, latency, estimated cost) and its usage is added to the
current AI node trace.
"""

import json
import time
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, Literal, TypeVar
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from confluo_core.ai_trace import current_trace
from confluo_core.llm.fake_provider import FakeProvider
from confluo_core.llm.pricing import estimate_cost
from confluo_core.llm.types import (
    ChatProvider,
    ChatRequest,
    ChatResult,
    EmbeddingProvider,
    EmbedInput,
    EmbedResult,
    Message,
    ToolSpec,
    Usage,
)
from confluo_core.settings import Settings
from confluo_core.tenancy import tenant_transaction

Tier = Literal["fast", "dialogue"]
M = TypeVar("M", bound=BaseModel)


class LLMConfigError(ValueError):
    pass


class RefusedError(RuntimeError):
    """The model (and its fallback) declined the request."""

    def __init__(self, category: str | None) -> None:
        super().__init__(f"model refused ({category or 'no category'})")
        self.category = category


def default_provider_factories(settings: Settings) -> dict[str, Callable[[], Any]]:
    """Providers are created on first use, so a missing key only matters for the
    provider a tier actually points at."""

    def anthropic() -> Any:
        from confluo_core.llm.anthropic_provider import AnthropicProvider

        key = settings.anthropic_api_key
        return AnthropicProvider(key.get_secret_value() if key else None)

    def voyage() -> Any:
        from confluo_core.llm.voyage_provider import VoyageProvider

        key = settings.voyage_api_key
        return VoyageProvider(key.get_secret_value() if key else None)

    return {"anthropic": anthropic, "voyage": voyage, "fake": FakeProvider}


# Value constraints structured outputs don't accept. They move into the field's
# description (so the model still sees them); Pydantic enforces them on the reply.
UNSUPPORTED = {
    "minimum": "at least {}",
    "maximum": "at most {}",
    "exclusiveMinimum": "more than {}",
    "exclusiveMaximum": "less than {}",
    "multipleOf": "a multiple of {}",
    "minLength": "at least {} characters",
    "maxLength": "at most {} characters",
}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema in the strict shape structured outputs need: every
    object closed (additionalProperties false) with all properties required, and
    no numeric or length constraints (see UNSUPPORTED)."""
    schema = model.model_json_schema()

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            limits = [
                text.format(node.pop(key)) for key, text in UNSUPPORTED.items() if key in node
            ]
            if limits:
                note = f"({', '.join(limits)})"
                node["description"] = (
                    f"{node['description']} {note}" if node.get("description") else note
                )
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)

    close(schema)
    return schema


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        pool: AsyncConnectionPool,
        providers: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._factories = default_provider_factories(settings)
        self._providers: dict[str, Any] = dict(providers or {})

    def _spec(self, tier: str) -> tuple[str, str]:
        spec = {
            "fast": self._settings.llm_fast,
            "dialogue": self._settings.llm_dialogue,
            "embedding": self._settings.llm_embedding,
        }[tier]
        provider, sep, model = spec.partition(":")
        if not sep or not model:
            raise LLMConfigError(f"tier {tier!r}: expected 'provider:model', got {spec!r}")
        return provider, model

    def _provider(self, name: str) -> Any:
        if name not in self._providers:
            factory = self._factories.get(name)
            if factory is None:
                raise LLMConfigError(f"unknown LLM provider {name!r}")
            self._providers[name] = factory()
        return self._providers[name]

    def model_for(self, tier: str) -> str:
        return self._spec(tier)[1]

    async def _log(
        self,
        tenant_id: UUID,
        *,
        purpose: str,
        tier: str,
        provider: str,
        model: str,
        usage: Usage,
        latency_ms: int,
        run_id: UUID | None,
    ) -> None:
        cost: Decimal | None = estimate_cost(model, usage)
        current_trace().add_usage(model, usage.input_tokens, usage.output_tokens)
        async with tenant_transaction(self._pool, tenant_id, actor="ai") as conn:
            await conn.execute(
                "insert into llm_usage (purpose, tier, provider, model, input_tokens,"
                " output_tokens, cache_read_tokens, cache_write_tokens, latency_ms,"
                " cost_usd, run_id)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    purpose,
                    tier,
                    provider,
                    model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cache_write_tokens,
                    latency_ms,
                    cost,
                    run_id,
                ),
            )

    async def chat(
        self,
        tenant_id: UUID,
        tier: Tier,
        purpose: str,
        *,
        system: str,
        messages: list[Message],
        tools: Sequence[ToolSpec] = (),
        output_schema: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        cache: bool = True,
        run_id: UUID | None = None,
    ) -> ChatResult:
        provider_name, model = self._spec(tier)
        provider: ChatProvider = self._provider(provider_name)
        started = time.perf_counter()
        result = await provider.chat(
            ChatRequest(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=list(tools),
                output_schema=output_schema,
                cache=cache,
                purpose=purpose,
            )
        )
        await self._log(
            tenant_id,
            purpose=purpose,
            tier=tier,
            provider=provider_name,
            model=result.model,
            usage=result.usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            run_id=run_id,
        )
        return result

    async def structured(
        self,
        tenant_id: UUID,
        tier: Tier,
        purpose: str,
        schema: type[M],
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        run_id: UUID | None = None,
    ) -> M:
        """A reply validated into `schema` (structured output)."""
        result = await self.chat(
            tenant_id,
            tier,
            purpose,
            system=system,
            messages=messages,
            output_schema=strict_schema(schema),
            max_tokens=max_tokens,
            run_id=run_id,
        )
        if result.stop_reason == "refusal":
            raise RefusedError(result.refusal_category)
        return schema.model_validate(json.loads(result.text))

    async def embed(
        self,
        tenant_id: UUID,
        purpose: str,
        texts: list[str],
        input_type: EmbedInput,
        run_id: UUID | None = None,
    ) -> EmbedResult:
        provider_name, model = self._spec("embedding")
        provider: EmbeddingProvider = self._provider(provider_name)
        started = time.perf_counter()
        result = await provider.embed(model, texts, input_type)
        await self._log(
            tenant_id,
            purpose=purpose,
            tier="embedding",
            provider=provider_name,
            model=model,
            usage=Usage(input_tokens=result.tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            run_id=run_id,
        )
        return result
