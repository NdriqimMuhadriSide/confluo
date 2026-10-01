"""Claude via the official Anthropic SDK."""

from typing import Any

import anthropic

from confluo_core.llm.types import (
    ChatRequest,
    ChatResult,
    Message,
    StopReason,
    ToolCall,
    Usage,
)

# Models whose safety classifiers can decline a request. For these we opt into
# Anthropic's server-side fallback, which re-runs a declined request on the
# recommended substitute model inside the same call.
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1", "claude-fable-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "refusal": "refusal",
}


def _content(message: Message) -> list[dict[str, Any]] | str:
    if message.role == "assistant" and message.tool_calls:
        blocks: list[dict[str, Any]] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in message.tool_calls
        ]
        return blocks
    if message.tool_results:
        # All results of one assistant turn go back in a single user message.
        return [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_call_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in message.tool_results
        ]
    return message.text


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        # Without an explicit key the SDK resolves credentials from the environment.
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def chat(self, request: ChatRequest) -> ChatResult:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": _content(m)} for m in request.messages],
        }
        if request.tools:
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "strict": True,
                }
                for t in request.tools
            ]
        if request.output_schema is not None:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": request.output_schema}
            }
        if request.cache:
            params["cache_control"] = {"type": "ephemeral"}

        if request.model in FALLBACK_MODELS:
            response = await self._client.beta.messages.create(
                **params, betas=[FALLBACK_BETA], fallbacks="default"
            )
        else:
            response = await self._client.messages.create(**params)

        stop: StopReason = _STOP.get(response.stop_reason or "", "other")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        u = response.usage
        details = getattr(response, "stop_details", None)
        return ChatResult(
            text="" if stop == "refusal" else text,
            tool_calls=[] if stop == "refusal" else calls,
            stop_reason=stop,
            model=response.model,
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_tokens=u.cache_creation_input_tokens or 0,
            ),
            refusal_category=getattr(details, "category", None) if stop == "refusal" else None,
        )
