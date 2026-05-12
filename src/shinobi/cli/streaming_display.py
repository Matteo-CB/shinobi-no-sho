"""Affichage progressif de la generation LLM via rich.live."""

from __future__ import annotations

from collections.abc import AsyncIterator

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from shinobi.llm.client import StreamChunk


async def stream_to_console(
    stream: AsyncIterator[StreamChunk], *, title: str | None = None,
) -> str:
    """Affiche le stream token par token et retourne le texte complet."""
    from shinobi.i18n import t

    if title is None:
        title = t("cli.streaming.default_title")
    console = Console()
    accumulated: list[str] = []
    with Live(refresh_per_second=20, console=console) as live:
        async for chunk in stream:
            if chunk.token:
                accumulated.append(chunk.token)
                live.update(Panel("".join(accumulated), title=title))
            if chunk.finished:
                break
    return "".join(accumulated)
