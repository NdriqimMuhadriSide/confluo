"""Deterministic provider for tests and offline development (no network, no cost).

Chat answers come from a script of `ChatResult`s (or a default echo); embeddings
are hashed bag-of-words vectors, so texts sharing words are near each other and
search behaves sensibly in tests.
"""

import hashlib
import math
import re
from collections.abc import Callable

from confluo_core.llm.types import ChatRequest, ChatResult, EmbedInput, EmbedResult, Usage

DIMENSIONS = 1024


# Deterministic answers per request purpose, registered by modules (e.g. the CRM
# intake graph's keyword-based "brain") so local dev and CI run without a real model.
RESPONDERS: dict[str, Callable[[ChatRequest], ChatResult]] = {}


def register_responder(purpose: str, responder: Callable[[ChatRequest], ChatResult]) -> None:
    RESPONDERS[purpose] = responder


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        script: list[ChatResult] | None = None,
        responder: Callable[[ChatRequest], ChatResult] | None = None,
    ) -> None:
        self.script = list(script or [])
        self.responder = responder
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if self.script:
            return self.script.pop(0)
        if self.responder:
            return self.responder(request)
        if request.purpose in RESPONDERS:
            return RESPONDERS[request.purpose](request)
        last = request.messages[-1].text if request.messages else ""
        text = '{"echo": true}' if request.output_schema else f"echo: {last}"
        tokens = max(1, len((request.system + last).split()))
        return ChatResult(
            text=text,
            tool_calls=[],
            stop_reason="end",
            model=request.model,
            usage=Usage(input_tokens=tokens, output_tokens=len(text.split())),
        )

    async def embed(self, model: str, texts: list[str], input_type: EmbedInput) -> EmbedResult:
        return EmbedResult(
            vectors=[embed_text(t) for t in texts],
            model=model,
            tokens=sum(len(t.split()) for t in texts),
        )


def embed_text(text: str) -> list[float]:
    vec = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
        vec[h % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
