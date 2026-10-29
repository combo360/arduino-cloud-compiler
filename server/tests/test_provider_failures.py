"""A broken assistant configuration must not take the server down.

This is exactly what happened on Render: one missing settings field raised at
startup, so compiling stopped too. The assistant is a feature; the compile
server is the product.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app import main as main_module  # noqa: E402


FAKE = Path(__file__).with_name("fake_arduino_cli.py")


def make_settings(tmp_path):
    return Settings(
        api_keys={"user-key": "alice"},
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=tmp_path / "data",
        llm_base_url="https://api.deepseek.com",
        llm_api_key="sk-x",
        llm_model="deepseek-flash",
    )


def test_a_provider_that_raises_still_leaves_a_working_server(tmp_path,
                                                             monkeypatch):
    def explode(settings):
        raise RuntimeError("provider setup is broken")

    monkeypatch.setattr(main_module.asist, "create_provider", explode)

    client = TestClient(main_module.create_app(make_settings(tmp_path)))

    # The product keeps working ...
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/v1/boards/installed",
                      headers={"X-API-Key": "user-key"}).status_code == 200
    # ... and the assistant reports itself as off instead of crashing.
    info = client.get("/v1/assistant/info", headers={"X-API-Key": "user-key"})
    assert info.json() == {"enabled": False, "model": None}


def test_settings_without_the_provider_field_are_tolerated():
    """An older config.py must not break a newer providers package."""
    from app.providers import choose_provider

    class OldSettings:
        llm_base_url = "https://api.deepseek.com"
        llm_api_key = "sk-x"
        llm_model = "deepseek-flash"

    assert choose_provider(OldSettings()).model == "deepseek-flash"
