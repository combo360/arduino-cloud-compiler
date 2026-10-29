import base64
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

FAKE = Path(__file__).with_name("fake_arduino_cli.py")
USER = {"X-API-Key": "user-key"}
OTHER = {"X-API-Key": "other-key"}
ADMIN = {"X-API-Key": "admin-key"}


def user_of(call: dict) -> str:
    """The library folder a fake arduino-cli call used, with / separators."""
    return Path(call["user"]).as_posix()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    builtin = tmp_path / "examples"
    (builtin / "01.Basics" / "Blink").mkdir(parents=True)
    (builtin / "01.Basics" / "Blink" / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n")
    (builtin / "02.Digital" / "Button").mkdir(parents=True)
    (builtin / "02.Digital" / "Button" / "Button.ino").write_text("// button\n")
    (builtin / "02.Digital" / "Button" / "notes.txt").write_text("wiring")
    settings = Settings(
        builtin_examples_dir=builtin,
        api_keys={"user-key": "alice", "other-key": "bob"},
        admin_keys={"admin-key": "admin"},
        arduino_cli=[sys.executable, str(FAKE)],  # works on Windows and Linux
        data_dir=tmp_path / "data",
        compiles_per_minute=3,
    )
    client = TestClient(create_app(settings))

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return client, calls, settings


def test_health_needs_no_key(env):
    client, _, _ = env
    assert client.get("/health").json() == {"status": "ok"}


def test_rejects_missing_and_wrong_key(env):
    client, _, _ = env
    assert client.get("/v1/boards/installed").status_code == 401
    assert client.get("/v1/boards/installed", headers={"X-API-Key": "nope"}).status_code == 401


def test_compile_returns_base64_hex(env):
    client, calls, _ = env
    r = client.post("/v1/compile", headers=USER,
                    json={"sketch": "void setup(){}\nvoid loop(){}", "fqbn": "arduino:avr:uno"})
    body = r.json()
    assert r.status_code == 200 and body["success"] is True
    assert base64.b64decode(body["hex"]).decode() == ":00000001FF\n"  # not with_bootloader
    assert body["format"] == "hex" and body["fqbn"] == "arduino:avr:uno"
    compile_call = [c for c in calls() if c["args"][0] == "compile"][0]
    assert user_of(compile_call).endswith("users/alice")


def test_compile_multi_file_sketch(env):
    client, _, _ = env
    r = client.post("/v1/compile", headers=USER, json={
        "sketch": "x", "main": "blink.ino", "fqbn": "arduino:avr:uno",
        "files": [{"name": "blink.ino", "content": "#include \"led.h\""},
                  {"name": "led.h", "content": "#pragma once"},
                  {"name": "led.cpp", "content": "int x;"}],
    })
    assert r.json()["success"] is True
    assert "led.cpp,led.h" in r.json()["logs"]


def test_compile_error_is_reported(env):
    client, _, _ = env
    r = client.post("/v1/compile", headers=USER,
                    json={"sketch": "SYNTAX_ERROR", "fqbn": "arduino:avr:uno"})
    assert r.status_code == 200
    assert r.json()["success"] is False and "error" in r.json()["logs"]
    assert "hex" not in r.json()


def test_esp32_returns_binary(env):
    client, _, _ = env
    r = client.post("/v1/compile", headers=USER,
                    json={"sketch": "x", "fqbn": "esp32:esp32:esp32"})
    body = r.json()
    assert body["format"] == "bin"
    assert base64.b64decode(body["binary"]) == b"\xe9BIN"
    # Every image with its offset, for flashing from the phone.
    assert body["flash"] == {"mode": "dio", "freq": "80m", "size": "4MB"}
    assert [(i["offset"], i["name"], base64.b64decode(i["data"])) for i in body["images"]] == [
        (0x1000, "sketch.ino.bootloader.bin", b"BOOT"),
        (0x8000, "sketch.ino.partitions.bin", b"PART"),
        (0xE000, "boot_app0.bin", b"OTA0"),
        (0x10000, "sketch.ino.bin", b"\xe9BIN"),
    ]


def test_flash_args_parsing(tmp_path):
    from app import cli as clib
    assert clib.parse_flash_args(tmp_path) is None
    (tmp_path / "flash_args").write_text("--flash-mode dio --flash-size 4MB\n0x0 ../etc/passwd\n")
    assert clib.parse_flash_args(tmp_path) is None  # outside the build folder
    (tmp_path / "app.bin").write_bytes(b"A")
    (tmp_path / "flash_args").write_text("--flash-size 8MB\n0x10000 app.bin\nbad line here\n")
    parsed = clib.parse_flash_args(tmp_path)
    assert parsed["settings"] == {"size": "8MB"}
    assert parsed["images"] == [{"offset": 0x10000, "name": "app.bin", "data": "QQ=="}]


@pytest.mark.parametrize("payload", [
    {"sketch": "x", "fqbn": "arduino:avr:uno; rm -rf /"},
    {"sketch": "x", "fqbn": "uno"},
    {"sketch": "x", "files": [{"name": "../evil.h", "content": ""}]},
    {"sketch": "x", "main": "main.cpp"},
])
def test_compile_rejects_bad_input(env, payload):
    client, _, _ = env
    assert client.post("/v1/compile", headers=USER, json=payload).status_code == 400


def test_compile_rate_limit(env):
    client, _, _ = env
    body = {"sketch": "x", "fqbn": "arduino:avr:uno"}
    codes = [client.post("/v1/compile", headers=USER, json=body).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # limits are per key
    assert client.post("/v1/compile", headers=OTHER, json=body).status_code == 200


def test_board_lists_match_app_format(env):
    client, _, _ = env
    installed = client.get("/v1/boards/installed", headers=USER).json()
    assert installed == [{"name": "Arduino Uno", "fqbn": "arduino:avr:uno",
                          "core": "arduino:avr", "version": "1.8.6", "installed": True}]
    found = client.get("/v1/boards/search", params={"query": "esp32"}, headers=USER).json()
    assert [b["name"] for b in found] == ["ESP32 Dev Module", "XIAO_ESP32C3"]
    assert found[0]["core"] == "esp32:esp32" and found[0]["installed"] is False


def test_core_changes_are_admin_only(env):
    client, _, _ = env
    assert client.post("/v1/boards/install", headers=USER, json={"core": "esp32:esp32"}).status_code == 403
    r = client.post("/v1/boards/install", headers=ADMIN, json={"core": "esp32:esp32"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert client.post("/v1/boards/install", headers=ADMIN, json={"core": "bad core"}).status_code == 400


def test_board_urls(env):
    client, calls, _ = env
    url = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
    assert client.post("/v1/boards/urls", headers=USER, json={"url": url}).status_code == 403
    assert client.post("/v1/boards/urls", headers=ADMIN, json={"url": "http://x/y.json"}).status_code == 400
    assert client.post("/v1/boards/urls", headers=ADMIN, json={"url": url}).json()["success"]
    assert client.get("/v1/boards/urls", headers=USER).json() == {"urls": [url]}
    client.get("/v1/boards/search", params={"query": "esp"}, headers=USER)
    assert url in calls()[-1]["args"]  # passed via --additional-urls
    assert client.request("DELETE", "/v1/boards/urls", headers=ADMIN, json={"url": url}).json()["urls"] == []


def test_libraries_are_per_user(env):
    client, calls, _ = env
    assert client.post("/v1/libraries/install", headers=USER,
                       json={"name": "MQTT", "version": "2.5.3"}).json()["success"]
    client.get("/v1/libraries/installed", headers=OTHER)
    install, listing = calls()[-2], calls()[-1]
    assert install["args"][:3] == ["lib", "install", "MQTT@2.5.3"]
    assert user_of(install).endswith("users/alice")
    assert user_of(listing).endswith("users/bob")


def test_library_search_and_versions(env):
    client, _, _ = env
    libs = client.get("/v1/libraries/search", params={"query": "mqtt"}, headers=USER).json()
    assert libs[0]["name"] == "MQTT" and libs[0]["version"] == "2.5.3"
    assert libs[0]["url"].startswith("https://github.com/")
    v = client.get("/v1/libraries/versions", params={"name": "MQTT"}, headers=USER).json()
    assert v == {"versions": ["2.5.3", "2.5.0", "2.4.8"]}
    installed = client.get("/v1/libraries/installed", headers=USER).json()
    assert installed[0]["name"] == "Servo" and installed[0]["installed"] is True


def test_anonymous_mode(tmp_path):
    settings = Settings(allow_anonymous=True, arduino_cli="true", data_dir=tmp_path)
    client = TestClient(create_app(settings))
    assert client.get("/v1/boards/urls").status_code == 200
    assert client.post("/v1/libraries/install", json={"name": "Servo"}).status_code == 403


def test_verbose_compile_passes_flag(env):
    client, calls, _ = env
    client.post("/v1/compile", headers=USER,
                json={"sketch": "x", "fqbn": "arduino:avr:uno", "verbose": True})
    compile_call = [c for c in calls() if c["args"][0] == "compile"][-1]
    assert "-v" in compile_call["args"]


@pytest.mark.skipif(shutil.which("clang-format") is None, reason="clang-format not installed")
def test_format_uses_arduino_style(env):
    client, _, _ = env
    r = client.post("/v1/format", headers=USER,
                    json={"code": "void setup(){pinMode(13,OUTPUT);}\n"})
    assert r.status_code == 200
    assert r.json()["formatted"] == "void setup() {\n  pinMode(13, OUTPUT);\n}\n"


def test_format_without_clang_format(env, tmp_path):
    client, _, settings = env
    settings.clang_format = str(tmp_path / "missing-clang-format")
    r = client.post("/v1/format", headers=USER, json={"code": "x"})
    assert r.status_code == 501


def test_examples_list_and_get(env):
    client, _, _ = env
    groups = client.get("/v1/examples", headers=USER).json()["groups"]
    names = {g["name"]: g for g in groups}
    assert names["01.Basics"]["examples"] == [{"id": "builtin/01.Basics/Blink", "name": "Blink"}]
    assert names["Servo"]["source"] == "library"
    assert [e["name"] for e in names["Servo"]["examples"]] == ["Sweep", "Knob"]

    ex = client.get("/v1/examples/get", params={"id": "builtin/02.Digital/Button"}, headers=USER).json()
    assert ex["name"] == "Button"
    assert [f["name"] for f in ex["files"]] == ["Button.ino", "notes.txt"]


def test_library_example_is_read_from_user_folder(env, tmp_path):
    client, _, settings = env
    sweep = settings.users_dir / "alice" / "libraries" / "Servo" / "examples" / "Sweep"
    sweep.mkdir(parents=True)
    (sweep / "Sweep.ino").write_text("#include <Servo.h>\n")
    ex = client.get("/v1/examples/get", params={"id": "lib/Servo/Sweep"}, headers=USER).json()
    assert ex["files"] == [{"name": "Sweep.ino", "content": "#include <Servo.h>\n"}]


@pytest.mark.parametrize("bad", ["builtin/../../etc", "builtin/01.Basics/../../x", "lib/Servo/../../../etc"])
def test_examples_reject_traversal(env, bad):
    client, _, _ = env
    assert client.get("/v1/examples/get", params={"id": bad}, headers=USER).status_code == 400


# -- features carried over from the original server ---------------------------

def test_missing_library_is_installed_and_compile_retried(env):
    client, calls, settings = env
    r = client.post("/v1/compile", headers=USER, json={
        "sketch": "#include <Servo.h>\n#include <MyMqtt.h>\nvoid setup(){}\nvoid loop(){}",
        "fqbn": "arduino:avr:uno"})
    body = r.json()
    assert body["success"] is True, body["logs"]
    assert "Installed library Servo (provides Servo.h)." in body["logs"]
    assert "Installed library MQTT (provides MyMqtt.h)." in body["logs"]
    installs = [c for c in calls() if c["args"][:2] == ["lib", "install"]]
    assert [c["args"][2] for c in installs] == ["Servo", "MQTT"]
    assert all(user_of(c).endswith("users/alice") for c in installs)
    # Servo comes from the built-in table, MQTT from `lib search provides:`.
    searches = [c["args"][2] for c in calls() if c["args"][:2] == ["lib", "search"]]
    assert searches == ["provides:MyMqtt.h"]
    # Installed for alice only.
    assert (settings.users_dir / "alice" / "libraries" / "Servo").is_dir()
    assert not (settings.users_dir / "bob").exists()


def test_unknown_header_is_reported_not_installed(env):
    client, calls, _ = env
    body = client.post("/v1/compile", headers=USER, json={
        "sketch": "#include <Nothing.h>\nvoid setup(){}", "fqbn": "arduino:avr:uno"}).json()
    assert body["success"] is False
    assert "No library in the Arduino index provides Nothing.h." in body["logs"]
    assert "fatal error: Nothing.h" in body["logs"]
    assert len([c for c in calls() if c["args"][0] == "compile"]) == 1


def test_core_headers_are_never_installed(env):
    client, calls, _ = env
    body = client.post("/v1/compile", headers=USER, json={
        "sketch": "#include <WiFi.h>\nvoid setup(){}", "fqbn": "arduino:avr:uno"}).json()
    assert body["success"] is False
    assert not [c for c in calls() if c["args"][0] == "lib"]


def test_auto_install_can_be_switched_off(env, tmp_path):
    _, calls, settings = env
    settings.auto_install_libraries = False
    client = TestClient(create_app(settings))
    body = client.post("/v1/compile", headers=USER, json={
        "sketch": "#include <Servo.h>\nvoid setup(){}", "fqbn": "arduino:avr:uno"}).json()
    assert body["success"] is False
    assert not [c for c in calls() if c["args"][:2] == ["lib", "install"]]


def test_anonymous_user_gets_a_hint_instead_of_installs(tmp_path, monkeypatch, env):
    _, calls, settings = env
    settings.allow_anonymous = True
    client = TestClient(create_app(settings))
    body = client.post("/v1/compile", json={
        "sketch": "#include <Servo.h>\nvoid setup(){}", "fqbn": "arduino:avr:uno"}).json()
    assert body["success"] is False
    assert "Missing libraries for Servo.h" in body["logs"]
    assert not [c for c in calls() if c["args"][:2] == ["lib", "install"]]


def test_shared_libraries_are_passed_to_compile(env):
    client, calls, settings = env
    (settings.shared_libraries_dir / "Shared").mkdir(parents=True)
    body = client.post("/v1/compile", headers=USER, json={
        "sketch": "#include <Shared.h>\nvoid setup(){}", "fqbn": "arduino:avr:uno"}).json()
    assert body["success"] is True, body["logs"]
    compile_call = [c for c in calls() if c["args"][0] == "compile"][0]["args"]
    assert compile_call[compile_call.index("--libraries") + 1] == str(settings.shared_libraries_dir)
    assert not [c for c in calls() if c["args"][:2] == ["lib", "install"]]


def test_deletes_accept_query_parameters_like_the_original_server(env):
    client, calls, _ = env
    assert client.delete("/v1/boards/uninstall", headers=ADMIN,
                         params={"core": "esp32:esp32"}).json()["success"] is True
    assert client.delete("/v1/libraries/uninstall", headers=USER,
                         params={"name": "Servo"}).json()["success"] is True
    url = "https://example.com/package_x_index.json"
    client.post("/v1/boards/urls", headers=ADMIN, json={"url": url})
    assert client.delete("/v1/boards/urls", headers=ADMIN,
                         params={"url": url}).json()["urls"] == []
    args = [c["args"][:3] for c in calls()]
    assert ["core", "uninstall", "esp32:esp32"] in args
    assert ["lib", "uninstall", "Servo"] in args
    # Bad input is still rejected.
    assert client.delete("/v1/boards/uninstall", headers=ADMIN,
                         params={"core": "x; rm -rf /"}).status_code == 400


def test_missing_header_parsing_and_library_choice():
    from app import cli as clib
    logs = ("/tmp/s/s.ino:1:10: fatal error: Servo.h: No such file or directory\n"
            "/tmp/s/s.ino:2:10: fatal error: Servo.h: No such file or directory\n"
            "x.cpp:3:10: fatal error: utility/twi.h: No such file or directory\n")
    assert clib.missing_headers(logs) == ["Servo.h", "utility/twi.h"]
    data = {"libraries": [{"name": "Some Fork"}, {"name": "Adafruit SSD1306"}]}
    assert clib.pick_library("Adafruit_SSD1306.h", data) == "Adafruit SSD1306"
    assert clib.pick_library("Other.h", data) == "Some Fork"
    assert clib.pick_library("Other.h", {"libraries": []}) is None


def test_default_board_urls_until_changed(env):
    _, _, settings = env
    url = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
    settings.default_board_urls = [url]
    client = TestClient(create_app(settings))
    assert client.get("/v1/boards/urls", headers=USER).json() == {"urls": [url]}
    assert client.request("DELETE", "/v1/boards/urls", headers=ADMIN,
                          params={"url": url}).json()["urls"] == []
    assert client.get("/v1/boards/urls", headers=USER).json() == {"urls": []}


def test_default_board_urls_from_env(monkeypatch):
    monkeypatch.setenv("IDROID_DEFAULT_BOARD_URLS", "https://a/x.json, https://b/y.json")
    assert Settings.from_env().default_board_urls == ["https://a/x.json", "https://b/y.json"]


def test_api_keys_tolerate_quotes_and_lines():
    from app.config import _parse_keys
    assert _parse_keys('"k1:alice,k2:bob"') == {"k1": "alice", "k2": "bob"}
    assert _parse_keys("k1:alice\n 'k2:bob' ") == {"k1": "alice", "k2": "bob"}
    assert _parse_keys(" k3 ") == {"k3": "user1"}
