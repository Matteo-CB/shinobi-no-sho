"""Pingue le serveur llama.cpp local et fait une generation test.

Usage : python scripts/test_llm.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shinobi.config import settings  # noqa: E402
from shinobi.llm.client import LLMClient, Message  # noqa: E402
from shinobi.logging_setup import configure_logging, get_logger  # noqa: E402

configure_logging()
logger = get_logger(__name__)


async def main() -> int:
    logger.info("llm_test_start", url=settings.llm_backend_url)
    async with LLMClient() as client:
        if not await client.health():
            logger.error("llm_unavailable", url=settings.llm_backend_url)
            print(f"Serveur LLM indisponible a {settings.llm_backend_url}")
            return 1

        response = await client.generate(
            messages=[
                Message(role="system", content="Tu es un narrateur sobre."),
                Message(
                    role="user",
                    content="En une phrase courte, decris la vue depuis le bureau du Hokage de Konoha.",
                ),
            ],
            max_tokens=120,
        )
        print("Reponse:", response.content)
        print("Finish:", response.finish_reason)
        print("Usage:", response.usage_tokens)
        logger.info("llm_test_ok", finish=response.finish_reason)
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
