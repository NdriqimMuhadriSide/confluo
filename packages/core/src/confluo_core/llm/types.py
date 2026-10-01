"""Provider-neutral request/response types for the LLM gateway."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant"]
StopReason = Literal["end", "max_tokens", "tool_use", "refusal", "other"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON schema with additionalProperties: false


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    """One conversation turn. Assistant turns that called tools carry `tool_calls`;
    the following user turn carries their `tool_results`."""

    role: Role
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ChatRequest:
    model: str
    system: str
    messages: list[Message]
    max_tokens: int = 4096
    tools: list[ToolSpec] = field(default_factory=list)
    # JSON schema the reply must match (structured output); the text is then JSON.
    output_schema: dict[str, Any] | None = None
    # Reuse the prompt prefix (system + tools + earlier turns) across calls.
    cache: bool = True


@dataclass(frozen=True)
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    model: str  # the model that answered (can differ after a refusal fallback)
    usage: Usage
    refusal_category: str | None = None


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    model: str
    tokens: int


EmbedInput = Literal["document", "query"]


class ChatProvider(Protocol):
    name: str

    async def chat(self, request: ChatRequest) -> ChatResult: ...


class EmbeddingProvider(Protocol):
    name: str

    async def embed(self, model: str, texts: list[str], input_type: EmbedInput) -> EmbedResult: ...
