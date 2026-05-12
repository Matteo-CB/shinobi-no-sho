"""Phase 6.5 : tests streaming_display.py.

Le helper `stream_to_console` lit un AsyncIterator[StreamChunk] et affiche
le texte token-par-token via rich.live. Test offline avec un fake stream.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from shinobi.cli.streaming_display import stream_to_console
from shinobi.llm.client import StreamChunk


async def _fake_stream(tokens: list[str]) -> AsyncIterator[StreamChunk]:
    """Yield 1 StreamChunk par token, finished=True sur le dernier."""
    for i, t in enumerate(tokens):
        yield StreamChunk(token=t, finished=(i == len(tokens) - 1))


@pytest.mark.asyncio
async def test_stream_to_console_concatenates_tokens() -> None:
    """stream_to_console accumule les tokens et retourne le texte complet."""
    tokens = ["Hello ", "world", "!"]
    result = await stream_to_console(_fake_stream(tokens), title="Test")
    assert result == "Hello world!"


@pytest.mark.asyncio
async def test_stream_to_console_empty_stream() -> None:
    """Stream sans token retourne string vide."""

    async def _empty() -> AsyncIterator[StreamChunk]:
        # Yield un seul chunk vide marque finished
        yield StreamChunk(token="", finished=True)

    result = await stream_to_console(_empty(), title="Empty")
    assert result == ""


@pytest.mark.asyncio
async def test_stream_to_console_stops_on_finished() -> None:
    """stream_to_console s'arrete au premier chunk finished=True."""

    async def _stream_with_extra() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(token="abc", finished=False)
        yield StreamChunk(token="def", finished=True)
        # Token apres finished : ne doit pas apparaitre dans le resultat
        yield StreamChunk(token="should_not_appear", finished=False)

    result = await stream_to_console(_stream_with_extra(), title="Stop")
    assert result == "abcdef"
    assert "should_not_appear" not in result


@pytest.mark.asyncio
async def test_stream_to_console_handles_unicode_french() -> None:
    """Tokens FR avec accents preserves correctement."""
    tokens = ["Le ninja ", "déclare : ", "« je vais ", "réussir »"]
    result = await stream_to_console(_fake_stream(tokens), title="FR")
    assert "déclare" in result
    assert "réussir" in result
    assert "«" in result
