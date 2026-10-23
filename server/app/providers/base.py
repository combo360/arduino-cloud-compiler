"""Provider-neutral pieces of the online assistant.

The rest of the server speaks one shape: a list of chat messages
(`{"role": …, "content": …}`) with the system prompt first, exactly what the
OpenAI API accepts. A provider adapts that to whatever its vendor wants on the
wire, so switching model is a server configuration change and nothing else.
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Protocol


class ProviderError(Exception):
    """The model provider refused or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Provider(Protocol):
    """One model vendor. `stream` yields answer text as it arrives."""

    name: str
    model: str

    def stream(self, messages: list[dict], *, max_tokens: int,
               temperature: float) -> AsyncIterator[str]: ...


def no_temperature() -> bool:
    """True when the model rejects a temperature (some reasoning models do).

    Set IDROID_LLM_NO_TEMPERATURE=1 for those, rather than editing code.
    """
    return os.environ.get("IDROID_LLM_NO_TEMPERATURE", "").lower() in (
        "1", "true", "yes")


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Pulls the system prompt out, which vendors place differently.

    OpenAI takes it as the first message; Anthropic wants a top-level field and
    refuses a system message in the list.
    """
    system = ""
    turns: list[dict] = []
    for message in messages:
        if message.get("role") == "system":
            system = str(message.get("content", ""))
        else:
            turns.append(message)
    return system, turns


def without_leading_assistant(turns: list[dict]) -> list[dict]:
    """Anthropic requires the conversation to start with the user.

    Our history starts wherever the thread was cut off, so a leading assistant
    turn is dropped rather than rejected by the vendor.
    """
    out = list(turns)
    while out and out[0].get("role") != "user":
        out.pop(0)
    return out
