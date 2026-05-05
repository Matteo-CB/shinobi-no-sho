"""Client HTTP vers llama.cpp (API compatible OpenAI).

Gere les retries, timeout, et la validation JSON Schema des sorties.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from shinobi.config import settings
from shinobi.errors import (
    LLMResponseError,
    LLMSchemaError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from shinobi.logging_setup import get_logger

logger = get_logger(__name__)

# Marqueurs Qwen3 pour desactiver le raisonnement explicite.
NO_THINK_TAG = "/no_think"

# Detection d'un bloc <think>...</think> que Qwen3 peut emettre meme avec /no_think.
THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


@dataclass(frozen=True)
class Message:
    """Message OpenAI-style envoye au LLM."""

    role: str  # system, user, assistant
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Reponse non streamee."""

    content: str
    raw_content: str
    finish_reason: str | None
    usage_tokens: dict[str, int]
    parsed_json: Any | None = None


@dataclass(frozen=True)
class StreamChunk:
    """Token elementaire d'un stream."""

    token: str
    finished: bool


class LLMClient:
    """Client async vers le serveur llama.cpp local."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        model_name: str | None = None,
    ) -> None:
        self._base_url = (base_url or settings.llm_backend_url).rstrip("/")
        self._timeout = timeout_seconds or settings.llm_timeout_seconds
        self._model = model_name or settings.llm_model_name
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> LLMClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        """Verifie que le serveur llama.cpp repond."""
        client = self._require_client()
        try:
            r = await client.get("/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate(
        self,
        messages: Iterable[Message],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 2,
    ) -> LLMResponse:
        """Generation non streamee. Si schema fourni, sortie validee JSON."""
        payload = self._build_chat_payload(
            messages=list(messages),
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                client = self._require_client()
                r = await client.post("/v1/chat/completions", json=payload)
                if r.status_code != 200:
                    raise LLMResponseError(f"HTTP {r.status_code}: {r.text[:300]}")
                return self._parse_response(r.json(), schema=schema)
            except httpx.ConnectError as exc:
                last_error = LLMUnavailableError(str(exc))
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError(str(exc))
            except (LLMSchemaError, LLMResponseError) as exc:
                last_error = exc
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    async def generate_streaming(
        self,
        messages: Iterable[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Generation token par token via Server-Sent Events."""
        payload = self._build_chat_payload(
            messages=list(messages),
            schema=None,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        client = self._require_client()
        try:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
                if r.status_code != 200:
                    raise LLMResponseError(f"HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    yield chunk
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(str(exc)) from exc

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    def _build_chat_payload(
        self,
        *,
        messages: list[Message],
        schema: dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        msgs = [{"role": m.role, "content": _maybe_no_think(m)} for m in messages]
        body: dict[str, Any] = {
            "model": self._model,
            "messages": msgs,
            "temperature": temperature
            if temperature is not None
            else (settings.llm_temperature_structured if schema else settings.llm_temperature),
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": stream,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_object",
                "schema": schema,
            }
        return body

    def _parse_response(
        self,
        data: dict[str, Any],
        *,
        schema: dict[str, Any] | None,
    ) -> LLMResponse:
        try:
            choice = data["choices"][0]
            raw = choice["message"]["content"] or ""
            finish = choice.get("finish_reason")
            usage = data.get("usage", {}) or {}
        except (KeyError, IndexError) as exc:
            raise LLMResponseError(f"Reponse malformee: {data}") from exc

        cleaned = THINK_BLOCK.sub("", raw).strip()
        parsed: Any | None = None
        if schema is not None:
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise LLMSchemaError(f"JSON invalide: {exc}\n{cleaned[:300]}") from exc

        return LLMResponse(
            content=cleaned,
            raw_content=raw,
            finish_reason=finish,
            usage_tokens={k: int(v) for k, v in usage.items() if isinstance(v, int)},
            parsed_json=parsed,
        )


def _maybe_no_think(message: Message) -> str:
    """Injecte /no_think dans le system prompt si Qwen3 thinking est desactive."""
    if not settings.llm_disable_thinking:
        return message.content
    if message.role != "system":
        return message.content
    if NO_THINK_TAG in message.content:
        return message.content
    return f"{message.content.rstrip()}\n\n{NO_THINK_TAG}"


def _parse_sse_line(line: str) -> StreamChunk | None:
    """Parse une ligne de Server-Sent Events au format llama.cpp."""
    if not line:
        return None
    if not line.startswith("data:"):
        return None
    payload_text = line[5:].strip()
    if payload_text == "[DONE]":
        return StreamChunk(token="", finished=True)
    try:
        payload = json.loads(payload_text)
        delta = payload["choices"][0].get("delta", {}) or {}
        token = delta.get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError):
        return None
    return StreamChunk(token=token, finished=False)
