"""The key-request flow: mint a key, email it to the operator, accept it.

The app is public, so the word is the only thing between a stranger and the
provider balance. These tests cover that it is generated, delivered, accepted,
and never leaked back to the caller.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

FAKE = Path(__file__).with_name("fake_arduino_cli.py")


class FakeProvider:
    model = "fake-model"

    async def stream(self, messages, *, max_tokens, temperature):
        yield "ok"


def make(tmp_path, **overrides):
    settings = Settings(
        api_keys={"user-key": "alice"},
        # The public app sends no key, so anonymous is the normal case.
        allow_anonymous=True,
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=tmp_path / "data",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="provider-key",
        llm_model="fake-model",
        **overrides,
    )
    app = create_app(settings)
    app.state.assistant_provider = FakeProvider()
    sent: list[str] = []

    def send_key(key, *, requested_by=""):
        sent.append(key)
        return "logged"

    app.state.assistant_send_key = send_key
    return TestClient(app), app, sent


def chat(client, word=None):
    headers = {"X-Assistant-Word": word} if word else {}
    return client.post("/v1/assistant/chat", headers=headers,
                       json={"task": "question", "message": "hi"})


def test_a_request_mints_a_key_and_emails_it(tmp_path):
    client, app, sent = make(tmp_path)
    response = client.post("/v1/assistant/request-key")

    assert response.status_code == 200
    assert response.json() == {"requested": True}
    assert len(sent) == 1
    assert sent[0].startswith("idroid-")
    assert app.state.assistant_keys.live_count() == 1


def test_the_key_is_never_returned_to_the_caller(tmp_path):
    # If the response contained it, the email would be pointless: anyone could
    # mint their own access.
    client, _, sent = make(tmp_path)
    response = client.post("/v1/assistant/request-key")
    assert sent[0] not in response.text


def test_the_mailed_key_opens_the_assistant(tmp_path):
    client, _, sent = make(tmp_path)
    client.post("/v1/assistant/request-key")

    assert chat(client).status_code == 403, "no word should be refused"
    assert chat(client, sent[0]).status_code == 200


def test_a_wrong_word_is_refused(tmp_path):
    client, _, _ = make(tmp_path)
    assert chat(client, "idroid-made-up-key").status_code == 403


def test_a_second_request_within_the_cooldown_does_not_send_another_mail(tmp_path):
    client, app, sent = make(tmp_path, assistant_key_cooldown_s=600)
    client.post("/v1/assistant/request-key")
    client.post("/v1/assistant/request-key")

    assert len(sent) == 1
    assert app.state.assistant_keys.live_count() == 1


def test_a_new_request_is_allowed_after_the_cooldown(tmp_path):
    client, app, sent = make(tmp_path, assistant_key_cooldown_s=0)
    client.post("/v1/assistant/request-key")
    client.post("/v1/assistant/request-key")

    assert len(sent) == 2
    assert app.state.assistant_keys.live_count() == 2


def test_preapproved_words_work_without_requesting_a_key(tmp_path):
    client, _, sent = make(tmp_path, assistant_words={"class-word": "class-a"})

    assert chat(client, "class-word").status_code == 200
    assert sent == [], "no mail should be sent for a preapproved word"


def test_the_daily_cap_stops_the_bill(tmp_path):
    client, _, sent = make(tmp_path, assistant_daily_cap=2)
    client.post("/v1/assistant/request-key")
    word = sent[0]

    assert chat(client, word).status_code == 200
    assert chat(client, word).status_code == 200
    blocked = chat(client, word)
    assert blocked.status_code == 429
    assert "daily limit" in blocked.json()["detail"]


def test_keys_are_hashed_on_disk(tmp_path):
    client, app, sent = make(tmp_path)
    client.post("/v1/assistant/request-key")

    stored = (tmp_path / "data" / "assistant_keys.json").read_text()
    assert sent[0] not in stored
    assert "hash" in stored
