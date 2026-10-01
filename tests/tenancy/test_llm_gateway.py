"""LLM gateway: provider swap by config, per-tenant usage log, structured output."""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from confluo_api.main import create_app
from confluo_core.ai_trace import AIRun, ai_node
from confluo_core.auth import TokenVerifier
from confluo_core.llm import LLMConfigError, LLMGateway, Message, RefusedError
from confluo_core.llm.anthropic_provider import AnthropicProvider
from confluo_core.llm.fake_provider import FakeProvider
from confluo_core.llm.types import ChatResult, Usage
from confluo_core.settings import Settings
from confluo_core.tenancy import tenant_transaction
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World


def settings(world: World, **tiers: str) -> Settings:
    return Settings(database_url=world.app_url, **tiers)  # type: ignore[arg-type]


async def test_provider_is_chosen_by_config_alone(pool: AsyncConnectionPool, world: World) -> None:
    fake = FakeProvider()
    gw = LLMGateway(settings(world, llm_fast="fake:tiny-model"), pool, {"fake": fake})
    result = await gw.chat(
        world.tenant_a, "fast", "test", system="s", messages=[Message("user", "hoi")]
    )
    assert result.text == "echo: hoi" and fake.requests[0].model == "tiny-model"

    # Same code, other config: the Anthropic provider is used (built lazily).
    gw2 = LLMGateway(settings(world, llm_fast="anthropic:claude-haiku-4-5"), pool)
    assert isinstance(gw2._provider("anthropic"), AnthropicProvider)
    assert gw2.model_for("fast") == "claude-haiku-4-5"


async def test_bad_config_is_rejected(pool: AsyncConnectionPool, world: World) -> None:
    with pytest.raises(LLMConfigError):
        await LLMGateway(settings(world, llm_fast="nope:model"), pool).chat(
            world.tenant_a, "fast", "x", system="s", messages=[Message("user", "x")]
        )
    with pytest.raises(LLMConfigError):
        LLMGateway(settings(world, llm_fast="no-colon"), pool).model_for("fast")


async def test_usage_is_logged_per_tenant_with_cost(
    pool: AsyncConnectionPool, world: World
) -> None:
    script = [
        ChatResult("a", [], "end", "claude-haiku-4-5", Usage(1_000_000, 200_000, 500_000, 0)),
        ChatResult("b", [], "end", "mystery-model", Usage(10, 2)),
    ]
    gw = LLMGateway(
        settings(world, llm_fast="fake:claude-haiku-4-5", llm_embedding="fake:voyage-3.5"),
        pool,
        {"fake": FakeProvider(script)},
    )
    purpose = f"intent-{uuid.uuid4().hex[:6]}"
    await gw.chat(world.tenant_a, "fast", purpose, system="s", messages=[Message("user", "x")])
    await gw.chat(world.tenant_b, "fast", purpose, system="s", messages=[Message("user", "y")])
    await gw.embed(world.tenant_a, purpose, ["free parking"], "document")

    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select tenant_id, tier, model, input_tokens, output_tokens, cache_read_tokens, cost_usd"
            " from llm_usage where purpose = %s order by id",
            (purpose,),
        ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (world.tenant_a, "fast", "claude-haiku-4-5"),
        (world.tenant_b, "fast", "mystery-model"),
        (world.tenant_a, "embedding", "voyage-3.5"),
    ]
    # 1M in x $1 + 0.5M cache reads x $0.10 + 0.2M out x $5 = $2.05
    assert rows[0][6] == Decimal("2.050000")
    assert rows[1][6] is None  # unknown price

    # Tenant A's manager sees only A's usage.
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute(
            "select distinct tenant_id from llm_usage where purpose = %s", (purpose,)
        )
        assert await cur.fetchall() == [(world.tenant_a,)]


class Intent(BaseModel):
    intent: str
    confidence: float


async def test_structured_output_and_refusal(pool: AsyncConnectionPool, world: World) -> None:
    script = [
        ChatResult('{"intent": "booking", "confidence": 0.93}', [], "end", "m", Usage(5, 5)),
        ChatResult("", [], "refusal", "m", Usage(5, 0), refusal_category="cyber"),
    ]
    fake = FakeProvider(script)
    gw = LLMGateway(settings(world, llm_fast="fake:m"), pool, {"fake": fake})
    parsed = await gw.structured(
        world.tenant_a,
        "fast",
        "intent",
        Intent,
        system="classify",
        messages=[Message("user", "haircut")],
    )
    assert parsed == Intent(intent="booking", confidence=0.93)
    schema = fake.requests[0].output_schema
    assert schema is not None and schema["additionalProperties"] is False
    with pytest.raises(RefusedError) as err:
        await gw.structured(
            world.tenant_a, "fast", "intent", Intent, system="s", messages=[Message("user", "x")]
        )
    assert err.value.category == "cyber"


async def test_usage_lands_in_the_ai_trace(pool: AsyncConnectionPool, world: World) -> None:
    gw = LLMGateway(
        settings(world, llm_dialogue="fake:claude-opus-5"), pool, {"fake": FakeProvider()}
    )
    run = AIRun(pool, world.tenant_a)

    @ai_node("compose_reply")
    async def compose(state: dict[str, Any], run: AIRun) -> dict[str, Any]:
        result = await gw.chat(
            run.tenant_id,
            "dialogue",
            "reply",
            system="be kind",
            messages=[Message("user", state["text"])],
            run_id=run.run_id,
        )
        return {"reply": result.text}

    await compose({"text": "Hallo daar"}, run)
    with psycopg.connect(world.owner_url) as conn:
        trace = conn.execute(
            "select model, input_tokens, output_tokens from ai_action where run_id = %s",
            (run.run_id,),
        ).fetchone()
        usage = conn.execute(
            "select count(*) from llm_usage where run_id = %s", (run.run_id,)
        ).fetchone()
    assert trace is not None and trace[0] == "claude-opus-5" and trace[1] > 0
    assert usage == (1,)


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    s = settings(world)
    verifier = TokenVerifier(s, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(s, token_verifier=verifier)) as c:
        yield c


def test_usage_endpoint(api: TestClient, world: World, make_token: MakeToken) -> None:
    h = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    body = api.get("/api/usage", headers=h).json()
    assert body["days"] == 30
    assert any(r["model"] == "claude-haiku-4-5" for r in body["rows"])
    assert Decimal(body["total_cost_usd"]) >= Decimal("2.05")
