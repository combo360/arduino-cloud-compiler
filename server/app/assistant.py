"""Online assistant: turn a structured request into a model call.

The app sends what it already knows (board, sketch, compiler output, question)
and this module decides what the model sees. Prompt design and model choice
therefore live on the server, so improving the assistant never needs an app
release, and the provider key never reaches a phone.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from pydantic import BaseModel, Field

TASK_NAMES = ("question", "compileError", "runtimeIssue", "completion")

# Kept in one place so the behaviour of the assistant can be reviewed and
# changed without touching the app.
SYSTEM_PROMPT = """You are the Arduino assistant built into i-Droid, an \
Arduino IDE for Android and a classroom tool.

SCOPE — stay inside it:
- You help with the user's Arduino project and with how to use i-Droid. Nothing \
else.
- If a request is about another subject, another framework, or general chat, \
refuse it in one sentence: say you only help with Arduino projects in i-Droid, \
then offer the closest help that is in scope. Never answer the off-topic part.

GROUND EVERY ANSWER IN THE CONTEXT YOU ARE GIVEN:
- The board, the user's sketch and the compiler or upload output are the truth. \
Refer to their actual pins and names, not to an example sketch.
- When the sketch is provided, never reply with a generic tutorial. Say what is \
wrong in that sketch.
- If something you need is missing (the wiring, a library version), say which \
detail you would need instead of guessing.

WHAT i-DROID CAN DO — do not claim more:
- Compiling happens on the i-Droid server, so the phone needs a network \
connection. Uploading is over USB OTG.
- Upload works for AVR boards (Uno, Nano, Pro Mini, Mega 2560, Leonardo, Micro) \
and classic ESP32 boards.
- ESP32-S2, ESP32-S3, ESP32-C3, ESP32-C6, STM32, RP2040 and SAMD boards can be \
compiled but NOT uploaded from the app yet. Say that plainly.
- The app also has a Serial Monitor, a Serial Plotter, Boards Manager, Library \
Manager, Examples, Auto Format, multiple tabs with auto-save, a dark theme, and \
English and Kiswahili interfaces. Do not promise features outside this list.

RULES FOR THE ANSWER:
- Answer only for the board described below. Use only pins, functions and \
libraries that exist on it.
- Keep it short: the cause, the fix in one code block, then one sentence of \
explanation.
- The user is usually a beginner. Plain language, and gloss any term you must \
use.
- If you are not sure, say so and suggest a check the user can do, for example \
with Serial.print.
- Never suggest connecting mains voltage. Warn about motors, relays, lithium \
batteries, and 5 V signals on 3.3 V boards.
- Answer in the language the user writes in."""

# One line telling the model what kind of turn this is.
_TASK_CONTEXT = {
    "question": "",
    "compileError": "The sketch below failed to compile or upload.",
    "runtimeIssue": "The sketch below compiles but does not work correctly.",
    "completion": "Complete the sketch at the cursor.",
}

# Used when the app sends no instruction of its own.
_TASK_DEFAULT_MESSAGE = {
    "question": "Answer the user's question.",
    "compileError": "Explain the first error simply, then show the smallest fix.",
    "runtimeIssue": "Point at the most likely cause in the code, and say what "
    "cannot be checked from the code alone.",
    "completion": "Return only the code to insert: no explanation, no code fence.",
}

_MAX_BOARD = 800
_MAX_SKETCH = 6000
_MAX_SUFFIX = 1500
_MAX_OUTPUT = 2000
_MAX_MESSAGE = 2000
# Enough for a follow-up to make sense without crowding out the sketch.
_MAX_HISTORY_TURNS = 8
_MAX_HISTORY_CHARS = 800


class HistoryTurn(BaseModel):
    """One earlier exchange. Anything but user/assistant is rejected."""

    model_config = {"extra": "forbid"}

    role: str = Field(pattern="^(user|assistant)$")
    content: str = ""


class ChatRequest(BaseModel):
    """One assistant turn. Unknown fields are rejected, not forwarded."""

    model_config = {"extra": "forbid"}

    task: str = "question"
    board: str = ""
    message: str = ""
    sketch: str | None = None
    sketch_prefix: str | None = None
    sketch_suffix: str | None = None
    compiler_output: str | None = None
    max_tokens: int = Field(default=500, ge=1, le=1200)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    history: list[HistoryTurn] = Field(default_factory=list)


class ProviderError(Exception):
    """The model provider refused or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Provider:
    """Streams from an OpenAI-compatible `/chat/completions` endpoint.

    OpenAI, Groq, Together, OpenRouter and a local Ollama or vLLM all speak
    this shape, so the server is not tied to one vendor.
    """

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
            "temperature": temperature,
            "stream": True,
        }
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
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        for choice in chunk.get("choices") or []:
                            content = (choice.get("delta") or {}).get("content")
                            if content:
                                yield content
        except httpx.HTTPError as error:
            raise ProviderError(
                f"Could not reach the model provider: {error}") from error


def create_provider(settings) -> Provider | None:
    """The provider for these settings, or None when the assistant is off."""
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return None
    return Provider(settings.llm_base_url, settings.llm_api_key, settings.llm_model)


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"


def messages_for(request: ChatRequest) -> list[dict]:
    """Builds the system + user messages for one request."""
    task = request.task if request.task in TASK_NAMES else "question"

    blocks: list[str] = []
    context = _TASK_CONTEXT[task]
    if context:
        blocks.append(context)
    if _clip(request.board, _MAX_BOARD):
        blocks.append(_clip(request.board, _MAX_BOARD))

    if task == "completion":
        before = _clip(request.sketch_prefix, _MAX_SKETCH)
        after = _clip(request.sketch_suffix, _MAX_SUFFIX)
        if before:
            blocks.append(f"Code before the cursor:\n```cpp\n{before}\n```")
        if after:
            blocks.append(f"Code after the cursor:\n```cpp\n{after}\n```")
    elif _clip(request.sketch, _MAX_SKETCH):
        blocks.append(f"The user's sketch:\n```cpp\n"
                      f"{_clip(request.sketch, _MAX_SKETCH)}\n```")

    if _clip(request.compiler_output, _MAX_OUTPUT):
        blocks.append("Compiler or upload output:\n```\n"
                      f"{_clip(request.compiler_output, _MAX_OUTPUT)}\n```")

    instruction = _clip(request.message, _MAX_MESSAGE) or \
        _TASK_DEFAULT_MESSAGE[task]
    blocks.append(instruction)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(request),
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def _history_messages(request: ChatRequest) -> list[dict]:
    """The tail of the conversation, trimmed, as chat messages."""
    out = []
    for turn in request.history[-_MAX_HISTORY_TURNS:]:
        content = _clip(turn.content, _MAX_HISTORY_CHARS)
        if content:
            out.append({"role": turn.role, "content": content})
    return out


def sse(payload: dict) -> str:
    """One server-sent event, matching what the app parses."""
    return "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"


async def stream_answer(provider: Provider, messages: list[dict],
                        request: ChatRequest) -> AsyncIterator[str]:
    """Wraps the provider stream so the response always closes cleanly.

    A failure mid-answer is reported as an `error` event rather than a broken
    connection: the app keeps what it has and offers Retry.
    """
    try:
        async for delta in provider.stream(
            messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        ):
            yield sse({"delta": delta})
    except ProviderError as error:
        yield sse({"error": str(error)})
    except Exception:  # noqa: BLE001 - never leave the client without an event
        yield sse({"error": "The assistant could not answer right now."})
    yield sse({"done": True})
