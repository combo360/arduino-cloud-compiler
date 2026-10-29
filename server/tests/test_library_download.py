"""Tests for the keyless, read-only library download endpoint.

The contract under test:

* no API key required (the app installs libraries on the phone),
* resolves ``name[, version]`` to the archive URL via the cached Arduino index,
* never installs anything and never writes into the core/library folders,
* 404 for unknown library/version, 400 for invalid input, 503 if the index is
  missing - never a 500,
* the parsed index is cached in-process (one read for many requests),
* the same rate limiter as compile/browse applies.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

FAKE = Path(__file__).with_name("fake_arduino_cli.py")

DHT_LATEST = "https://downloads.arduino.cc/libraries/github.com/adafruit/DHT_sensor_library-1.4.6.zip"
DHT_OLD = "https://downloads.arduino.cc/libraries/github.com/adafruit/DHT_sensor_library-1.4.5.zip"
TM1637 = "https://downloads.arduino.cc/libraries/github.com/avishorp/TM1637-1.2.0.zip"

INDEX = {
    "libraries": [
        {
            "name": "DHT sensor library",
            "releases": {
                "1.4.6": {"url": DHT_LATEST},
                "1.4.5": {"url": DHT_OLD},
            },
        },
        {
            "name": "TM1637",
            # Older index revisions nested the URL under `resources`.
            "releases": {"1.2.0": {"resources": [{"url": TM1637}]}},
        },
    ]
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    builtin = tmp_path / "examples"
    (builtin / "01.Basics" / "Blink").mkdir(parents=True)
    (builtin / "01.Basics" / "Blink" / "Blink.ino").write_text("void setup() {}\n")

    data = tmp_path / "data"
    data.mkdir()
    (data / "library_index.json").write_text(json.dumps(INDEX))

    settings = Settings(
        builtin_examples_dir=builtin,
        api_keys={"user-key": "alice"},
        admin_keys={"admin-key": "admin"},
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=data,
        allow_anonymous=True,
    )
    client = TestClient(create_app(settings))

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return client, calls, settings


def get(client, **params):
    """GET without following the redirect, so we can assert on the 302 itself."""
    return client.get("/v1/libraries/download", params=params, follow_redirects=False)


def test_redirects_to_latest_archive_without_a_key(env):
    client, _, _ = env
    r = get(client, name="DHT sensor library")
    assert r.status_code == 302
    assert r.headers["location"] == DHT_LATEST
    assert "max-age" in r.headers.get("cache-control", "")


def test_pins_an_explicit_version(env):
    client, _, _ = env
    r = get(client, name="DHT sensor library", version="1.4.5")
    assert r.status_code == 302
    assert r.headers["location"] == DHT_OLD


def test_accepts_url_encoded_and_case_insensitive_names(env):
    client, _, _ = env
    assert get(client, name="tm1637").headers["location"] == TM1637
    assert get(client, name="dht SENSOR library").headers["location"] == DHT_LATEST


def test_unknown_library_is_404_with_a_clear_reason(env):
    client, _, _ = env
    r = get(client, name="NoSuchLibrary")
    assert r.status_code == 404
    assert "not found in the Arduino index" in r.json()["detail"]


def test_unknown_version_is_404_naming_the_version(env):
    client, _, _ = env
    r = get(client, name="DHT sensor library", version="9.9.9")
    assert r.status_code == 404
    assert "9.9.9" in r.json()["detail"]


@pytest.mark.parametrize("params", [{"name": ""}, {"name": "bad\x01name"},
                                   {"name": "DHT", "version": "no spaces"}])
def test_rejects_invalid_input_with_400(env, params):
    client, _, _ = env
    assert get(client, **params).status_code == 400


def test_never_installs_a_library(env):
    """Resolving a URL must not touch shared state: no `lib install` call."""
    client, calls, _ = env
    get(client, name="TM1637")
    assert not [c for c in calls() if c["args"][:2] == ["lib", "install"]]


def test_index_is_read_once_for_many_requests(env):
    client, _, _ = env
    index = client.app.state.library_index
    assert index.loads == 0
    for _ in range(3):
        assert get(client, name="TM1637").status_code == 302
    assert index.loads == 1, "the parsed index must be cached between requests"


def test_index_cache_can_be_forced_to_reload(env):
    client, _, _ = env
    index = client.app.state.library_index
    get(client, name="TM1637")
    index.force_reload = True
    get(client, name="TM1637")
    assert index.loads == 2


def test_missing_index_reports_503_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "calls.jsonl"))
    builtin = tmp_path / "examples"
    builtin.mkdir()
    data = tmp_path / "empty-data"
    data.mkdir()
    settings = Settings(
        builtin_examples_dir=builtin,
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=data,
        allow_anonymous=True,
    )
    client = TestClient(create_app(settings))
    r = get(client, name="TM1637")
    assert r.status_code == 503
    assert "library index" in r.json()["detail"].lower()


def test_rate_limiter_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "calls.jsonl"))
    builtin = tmp_path / "examples"
    builtin.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "library_index.json").write_text(json.dumps(INDEX))
    settings = Settings(
        builtin_examples_dir=builtin,
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=data,
        allow_anonymous=True,
        requests_per_minute=1,
    )
    client = TestClient(create_app(settings))
    assert get(client, name="TM1637").status_code == 302
    assert get(client, name="TM1637").status_code == 429

def test_follows_the_server_anonymous_policy(tmp_path, monkeypatch):
    """Keyless only where anonymous access is on (as configured in render.yaml).

    With IDROID_ALLOW_ANONYMOUS=false the endpoint falls under the same auth as
    compile/browse: a key is required, and a valid one still gets the redirect.
    """
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "calls.jsonl"))
    builtin = tmp_path / "examples"
    builtin.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "library_index.json").write_text(json.dumps(INDEX))
    settings = Settings(
        builtin_examples_dir=builtin,
        api_keys={"user-key": "alice"},
        arduino_cli=[sys.executable, str(FAKE)],
        data_dir=data,
        allow_anonymous=False,
    )
    client = TestClient(create_app(settings))
    assert get(client, name="TM1637").status_code == 401
    r = client.get("/v1/libraries/download", params={"name": "TM1637"},
                   headers={"X-API-Key": "user-key"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == TM1637