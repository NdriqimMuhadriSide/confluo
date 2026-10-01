"""Embeddings from Voyage AI (ARCHITECTURE.md §5, decision 6)."""

from typing import Any

from voyageai.client_async import AsyncClient

from confluo_core.llm.types import EmbedInput, EmbedResult

DIMENSIONS = 1024  # matches crm_knowledge_chunk.embedding vector(1024)


class VoyageProvider:
    name = "voyage"

    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        self._client = client or AsyncClient(api_key=api_key)

    async def embed(self, model: str, texts: list[str], input_type: EmbedInput) -> EmbedResult:
        result = await self._client.embed(
            texts, model=model, input_type=input_type, output_dimension=DIMENSIONS
        )
        return EmbedResult(
            vectors=[[float(x) for x in v] for v in result.embeddings],
            model=model,
            tokens=result.total_tokens,
        )
