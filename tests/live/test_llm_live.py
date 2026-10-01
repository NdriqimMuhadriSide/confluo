"""Live smoke tests against the real providers: one tiny call each.

Skipped unless the keys are set (ANTHROPIC_API_KEY, VOYAGE_API_KEY, e.g. in .env).
Run with `make smoke-llm`; each run costs a fraction of a cent.
"""

import pytest
from pydantic import BaseModel

from confluo_core.llm.anthropic_provider import AnthropicProvider
from confluo_core.llm.gateway import strict_schema
from confluo_core.llm.types import ChatRequest, Message
from confluo_core.llm.voyage_provider import VoyageProvider
from confluo_core.settings import Settings

settings = Settings()
needs_anthropic = pytest.mark.skipif(
    settings.anthropic_api_key is None, reason="ANTHROPIC_API_KEY not set"
)
needs_voyage = pytest.mark.skipif(settings.voyage_api_key is None, reason="VOYAGE_API_KEY not set")


class Intent(BaseModel):
    intent: str
    language: str


@needs_anthropic
async def test_fast_tier_structured_output() -> None:
    assert settings.anthropic_api_key
    provider = AnthropicProvider(settings.anthropic_api_key.get_secret_value())
    model = settings.llm_fast.partition(":")[2]
    result = await provider.chat(
        ChatRequest(
            model=model,
            system="Classify the customer's message. intent: booking, faq or other. "
            "language: ISO 639-1 code.",
            messages=[Message("user", "Kan ik vrijdag om 10 uur langskomen voor een knipbeurt?")],
            output_schema=strict_schema(Intent),
            max_tokens=256,
            cache=False,
        )
    )
    assert result.stop_reason == "end", result
    parsed = Intent.model_validate_json(result.text)
    assert parsed.intent == "booking" and parsed.language == "nl"
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0


@needs_anthropic
async def test_dialogue_tier_answers() -> None:
    assert settings.anthropic_api_key
    provider = AnthropicProvider(settings.anthropic_api_key.get_secret_value())
    model = settings.llm_dialogue.partition(":")[2]
    result = await provider.chat(
        ChatRequest(
            model=model,
            system="You are the receptionist of a hair salon in Ghent. Reply in one short sentence.",
            messages=[Message("user", "Zijn jullie op zondag open?")],
            max_tokens=1024,
            cache=False,
        )
    )
    assert result.stop_reason == "end", result
    assert result.text.strip()


@needs_voyage
async def test_voyage_embeddings() -> None:
    assert settings.voyage_api_key
    provider = VoyageProvider(settings.voyage_api_key.get_secret_value())
    model = settings.llm_embedding.partition(":")[2]
    result = await provider.embed(
        model, ["Gratis parkeren achter het salon", "We accept cards"], "document"
    )
    assert len(result.vectors) == 2 and len(result.vectors[0]) == 1024
    assert result.tokens > 0
