"""Streaming token par token vers la CLI ou un autre consommateur."""

from __future__ import annotations

from collections.abc import AsyncIterator

from shinobi.llm.client import LLMClient, Message, StreamChunk


async def stream_completion(
    client: LLMClient,
    messages: list[Message],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[StreamChunk]:
    """Wrapper minimal autour du client en mode streaming."""
    async for chunk in client.generate_streaming(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        yield chunk
