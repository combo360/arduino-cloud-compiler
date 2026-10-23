"""Anthropic's Messages API, which is not OpenAI-shaped.

Three differences matter: `x-api-key` instead of a bearer token, the system
prompt is a top-level field (and the conversation must start with the user), and
the stream carries typed events rather than `choices[].delta.content`.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import (ProviderError, no_temperature, split_system,
                   without_leading_assistant)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.anthropic.com",
                 version: str = "2023-06-01",
                 timeout: float = 90.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._api_key = api_key
        self._version = version
        self.model = model
        self._timeout = timeout

    async def stream(self, messages: list[dict], *, max_tokens: int,
                     temperature: float) -> AsyncIterator[str]:
        system, turns = split_system(messages)
        turns = without_leading_assistant(turns)
        if not turns:
            raise ProviderError("The assistant had nothing to send.")

        payload: dict = {
            "model": self.model,
            # Required by Anthropic, always sent by this server.
            "max_tokens": max_tokens,
            "messages": turns,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if not no_temperature():
            payload["temperature"] = temperature

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
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
                        kind, text = parse_event(line)
                        if kind == "done":
                            return
                        if text:
                            yield text
        except httpx.HTTPError as error:
            raise ProviderError(
                f"Could not reach the model provider: {error}") from error


def parse_event(line: str) -> tuple[str, str]:
    """One SSE line → ("text", chunk), ("done", "") or ("", "").

    Kept free of the transport so it can be tested with recorded lines.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return "", ""
    try:
        event = json.loads(line[5:].strip())
    except json.JSONDecodeError:
        return "", ""
    kind = event.get("type")
    if kind == "message_stop":
        return "done", ""
    if kind == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return "text", delta.get("text") or ""
    return "", ""
