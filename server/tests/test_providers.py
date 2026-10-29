"""Both provider shapes, tested without a network.

The wire formats are the risky part of a model switch, so the two stream
parsers and the request shaping are tested directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.providers import choose_provider  # noqa: E402
from app.providers.anthropic import AnthropicProvider, parse_event as anthropic_event  # noqa: E402,E501
from app.providers.base import split_system, without_leading_assistant  # noqa: E402
from app.providers.openai import DONE, OpenAiProvider, parse_event as openai_event  # noqa: E402,E501


# -- OpenAI-shaped -----------------------------------------------------------

def test_openai_stream_parser_reads_deltas():
    assert openai_event('data: {"choices":[{"delta":{"content":"Hi"}}]}') == "Hi"
    assert openai_event("data: [DONE]") is DONE
    assert openai_event("") is None
    assert openai_event(": keep-alive") is None
    assert openai_event('data: {"choices":[{"delta":{}}]}') is None


# -- Anthropic ---------------------------------------------------------------

def test_anthropic_stream_parser_reads_text_deltas():
    line = ('data: {"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hello"}}')
    assert anthropic_event(line) == ("text", "Hello")
    assert anthropic_event('data: {"type":"message_stop"}') == ("done", "")
    assert anthropic_event("event: message_start") == ("", "")
    assert anthropic_event("data: not json") == ("", "")


def test_anthropic_ignores_non_text_deltas():
    line = ('data: {"type":"content_block_delta",'
            '"delta":{"type":"thinking_delta","thinking":"hmm"}}')
    assert anthropic_event(line) == ("", "")


# -- request shaping ---------------------------------------------------------

def test_split_system_pulls_the_prompt_out():
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    system, turns = split_system(messages)
    assert system == "be brief"
    assert turns == [{"role": "user", "content": "hi"}]


def test_split_system_tolerates_no_system_prompt():
    system, turns = split_system([{"role": "user", "content": "hi"}])
    assert system == ""
    assert turns == [{"role": "user", "content": "hi"}]


def test_a_leading_assistant_turn_is_dropped():
    # Anthropic rejects a conversation that does not start with the user.
    turns = [{"role": "assistant", "content": "earlier"},
             {"role": "user", "content": "now"}]
    assert without_leading_assistant(turns) == [{"role": "user", "content": "now"}]


# -- choosing ----------------------------------------------------------------

def test_anthropic_needs_no_base_url():
    settings = Settings(llm_provider="anthropic", llm_api_key="sk-ant",
                        llm_model="claude-sonnet-4-5")
    provider = choose_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-4-5"


def test_anthropic_is_inferred_from_the_base_url():
    settings = Settings(llm_base_url="https://api.anthropic.com",
                        llm_api_key="sk-ant", llm_model="claude")
    assert isinstance(choose_provider(settings), AnthropicProvider)


def test_openai_shaped_stays_the_default():
    settings = Settings(llm_base_url="https://api.deepseek.com",
                        llm_api_key="sk-x", llm_model="deepseek-flash")
    assert isinstance(choose_provider(settings), OpenAiProvider)


def test_no_key_or_model_means_the_assistant_is_off():
    assert choose_provider(Settings(llm_base_url="https://api.deepseek.com")) is None
    assert choose_provider(
        Settings(llm_api_key="sk-x", llm_model="m")) is None, \
        "an OpenAI-shaped provider without a base URL is not usable"


def test_switching_model_is_configuration_only():
    # Same code path, three different vendors, no app change.
    for base, model in [("https://api.openai.com/v1", "gpt-4o-mini"),
                        ("https://api.groq.com/openai/v1", "llama-3.3-70b"),
                        ("https://openrouter.ai/api/v1", "anthropic/claude-x")]:
        provider = choose_provider(Settings(
            llm_base_url=base, llm_api_key="k", llm_model=model))
        assert isinstance(provider, OpenAiProvider)
        assert provider.model == model
