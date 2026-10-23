"""OpenAI-compatible providers.

OpenAI, Groq, Together, OpenRouter, DeepSeek, a local Ollama or vLLM and
Gemini's compatibility endpoint all speak this shape, so one class covers them:
only the base URL and model change.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import ProviderError, no_temperature


class OpenAiProvider:
    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 90.0) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.model = model
        self._timeout = timeout

    async def stream(self, messages: list[dict], *, max_tokens: int,
                     temperature: float) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if not no_temperature():
            payload["temperature"] = temperature
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", self._url, headers=headers,
                                         json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "replace")
                        raise ProviderError(
                            f"Model provider answered {response.status_code}: "
                            f"{body[:200]}",
                            response.status_code,
                        )
                    async for line in response.aiter_lines():
                        delta = parse_event(line)
                        if delta is None:
                            continue
                        if delta == DONE:
                            return
                        if delta:
                            yield delta
        except httpx.HTTPError as error:
            raise ProviderError(
                f"Could not reach the model provider: {error}") from error


DONE = object()


def parse_event(line: str) -> str | None | object:
    """One SSE line → the text it carries, DONE, or None for anything else.

    Separated from the transport so it can be tested against recorded chunks.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return DONE
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return None
    for choice in chunk.get("choices") or []:
        content = (choice.get("delta") or {}).get("content")
        if content:
            return content
    return None
