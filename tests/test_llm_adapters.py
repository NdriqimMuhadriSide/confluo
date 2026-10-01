"""Provider adapters, checked against stub SDK clients (no network)."""

from types import SimpleNamespace
from typing import Any

import pytest

from confluo_core.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from confluo_core.llm.fake_provider import embed_text
from confluo_core.llm.gateway import strict_schema
from confluo_core.llm.types import ChatRequest, Message, ToolCall, ToolResult, ToolSpec
from confluo_core.llm.voyage_provider import VoyageProvider


class StubMessages:
    def __init__(self, response: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response

    async def create(self, **params: Any) -> Any:
        self.calls.append(params)
        return self.response


def anthropic_response(**overrides: Any) -> Any:
    base = {
        "content": [SimpleNamespace(type="text", text="Hallo!")],
        "stop_reason": "end_turn",
        "model": "claude-haiku-4-5",
        "usage": SimpleNamespace(
            input_tokens=100,
            output_tokens=7,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=0,
        ),
        "stop_details": None,
    }
    return SimpleNamespace(**(base | overrides))


def stub_client(response: Any) -> Any:
    return SimpleNamespace(
        messages=StubMessages(response), beta=SimpleNamespace(messages=StubMessages(response))
    )


TOOL = ToolSpec(
    "find_slots",
    "Free appointment slots",
    {
        "type": "object",
        "properties": {"day": {"type": "string"}},
        "required": ["day"],
        "additionalProperties": False,
    },
)


async def test_anthropic_request_shape_and_result() -> None:
    client = stub_client(anthropic_response())
    result = await AnthropicProvider(client=client).chat(
        ChatRequest(
            model="claude-haiku-4-5",
            system="You are the salon assistant.",
            messages=[
                Message("user", "Is Friday free?"),
                Message(
                    "assistant",
                    "Let me check.",
                    tool_calls=(ToolCall("t1", "find_slots", {"day": "fri"}),),
                ),
                Message("user", tool_results=(ToolResult("t1", '["10:00"]'),)),
            ],
            tools=[TOOL],
            output_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
    )
    [params] = client.messages.calls
    assert client.beta.messages.calls == []  # haiku: no refusal fallback
    assert params["model"] == "claude-haiku-4-5"
    assert params["system"] == "You are the salon assistant."
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["tools"][0]["strict"] is True
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["messages"][1]["content"][1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "find_slots",
        "input": {"day": "fri"},
    }
    assert params["messages"][2]["content"][0]["type"] == "tool_result"
    assert (result.text, result.stop_reason, result.model) == ("Hallo!", "end", "claude-haiku-4-5")
    assert (result.usage.input_tokens, result.usage.cache_read_tokens) == (100, 80)


async def test_opus_uses_server_side_refusal_fallback() -> None:
    client = stub_client(anthropic_response(model="claude-opus-5"))
    await AnthropicProvider(client=client).chat(
        ChatRequest(model="claude-opus-5", system="s", messages=[Message("user", "hi")])
    )
    assert client.messages.calls == []
    [params] = client.beta.messages.calls
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"


async def test_refusal_is_reported_without_content() -> None:
    client = stub_client(
        anthropic_response(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    )
    result = await AnthropicProvider(client=client).chat(
        ChatRequest(model="claude-haiku-4-5", system="s", messages=[Message("user", "x")])
    )
    assert (result.stop_reason, result.text, result.refusal_category) == ("refusal", "", "cyber")


async def test_tool_calls_are_returned() -> None:
    client = stub_client(
        anthropic_response(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="tool_use", id="t9", name="find_slots", input={"day": "mon"})
            ],
        )
    )
    result = await AnthropicProvider(client=client).chat(
        ChatRequest(
            model="claude-haiku-4-5", system="s", messages=[Message("user", "x")], tools=[TOOL]
        )
    )
    assert result.stop_reason == "tool_use"
    assert result.tool_calls == [ToolCall("t9", "find_slots", {"day": "mon"})]


async def test_voyage_adapter() -> None:
    class StubVoyage:
        calls: list[dict[str, Any]] = []

        async def embed(self, texts: list[str], **kw: Any) -> Any:
            self.calls.append({"texts": texts, **kw})
            return SimpleNamespace(embeddings=[[0.1] * 1024 for _ in texts], total_tokens=12)

    stub = StubVoyage()
    result = await VoyageProvider(client=stub).embed("voyage-3.5", ["a", "b"], "document")
    assert stub.calls == [
        {
            "texts": ["a", "b"],
            "model": "voyage-3.5",
            "input_type": "document",
            "output_dimension": 1024,
        }
    ]
    assert len(result.vectors) == 2 and len(result.vectors[0]) == 1024 and result.tokens == 12


def test_strict_schema_closes_every_object() -> None:
    from pydantic import BaseModel

    class Slot(BaseModel):
        day: str
        hour: int | None = None

    class Intent(BaseModel):
        intent: str
        slots: list[Slot]

    schema = strict_schema(Intent)
    assert schema["additionalProperties"] is False and schema["required"] == ["intent", "slots"]
    slot = schema["$defs"]["Slot"]
    assert slot["additionalProperties"] is False and slot["required"] == ["day", "hour"]


def test_fake_embeddings_are_normalised_and_word_based() -> None:
    a, b, c = embed_text("free parking"), embed_text("parking is free"), embed_text("card payment")

    def cos(x: list[float], y: list[float]) -> float:
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert cos(a, a) == pytest.approx(1.0)
    assert cos(a, b) > cos(a, c)
