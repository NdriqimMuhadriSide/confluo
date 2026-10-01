"""LLM gateway: provider-neutral chat, structured output, tools and embeddings."""

from confluo_core.llm.gateway import LLMConfigError, LLMGateway, RefusedError, Tier
from confluo_core.llm.types import (
    ChatResult,
    EmbedResult,
    Message,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

__all__ = [
    "ChatResult",
    "EmbedResult",
    "LLMConfigError",
    "LLMGateway",
    "Message",
    "RefusedError",
    "Tier",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "Usage",
]
