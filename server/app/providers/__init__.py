"""Picks the provider for the configured model.

Switching model is a server-side change only:
    IDROID_LLM_PROVIDER=anthropic|openai      (optional; inferred when omitted)
    IDROID_LLM_API_KEY=…
    IDROID_LLM_MODEL=…
    IDROID_LLM_BASE_URL=…                     (OpenAI-shaped providers only)
"""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai import OpenAiProvider

__all__ = ["Provider", "ProviderError", "choose_provider"]

# Hosts that are not OpenAI-shaped, inferred when IDROID_LLM_PROVIDER is unset.
_NATIVE_HOSTS = {"anthropic": "anthropic", "claude": "anthropic"}


def _infer(settings) -> str:
    if settings.llm_base_url and "anthropic" in settings.llm_base_url.lower():
        return "anthropic"
    return "openai"


def choose_provider(settings) -> Provider | None:
    """The provider for these settings, or None when the assistant is off."""
    if not (settings.llm_api_key and settings.llm_model):
        return None

    kind = (settings.llm_provider or _infer(settings)).strip().lower()
    for fragment, name in _NATIVE_HOSTS.items():
        if kind == fragment:
            kind = name

    if kind == "anthropic":
        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            # An empty base URL means the default endpoint.
            base_url=settings.llm_base_url or "https://api.anthropic.com",
        )

    # Everything else is OpenAI-shaped and needs a base URL.
    if not settings.llm_base_url:
        return None
    return OpenAiProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
