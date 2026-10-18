"""Thin async wrapper around arduino-cli plus tolerant JSON parsers.

arduino-cli's JSON output changed shape between 0.3x and 1.x; the parsers
below accept both so the server keeps working across upgrades.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def logs(self) -> str:
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()

    def json(self) -> Any:
        try:
            return json.loads(self.stdout or "null")
        except json.JSONDecodeError:
            return None


class ArduinoCli:
    def __init__(self, executable: str, data_dir: Path):
        self.executable = executable
        self.data_dir = data_dir

    def _env(self, user_dir: Path | None) -> dict[str, str]:
        env = dict(os.environ)
        # Cores and tools are shared; libraries live in the user's folder.
        env["ARDUINO_DIRECTORIES_DATA"] = str(self.data_dir / "arduino15")
        env["ARDUINO_DIRECTORIES_DOWNLOADS"] = str(self.data_dir / "staging")
        env["ARDUINO_DIRECTORIES_USER"] = str(user_dir or (self.data_dir / "shared_user"))
        # Compiled cores are cached on the volume so they survive restarts.
        env["ARDUINO_BUILD_CACHE_PATH"] = str(self.data_dir / "build-cache")
        env.setdefault("ARDUINO_UPDATER_ENABLE_NOTIFICATION", "false")
        return env

    async def run(
        self,
        *args: str,
        user_dir: Path | None = None,
        timeout: float = 120,
        additional_urls: list[str] | None = None,
    ) -> CliResult:
        argv = [self.executable, *args]
        if additional_urls:
            argv += ["--additional-urls", ",".join(additional_urls)]
        if user_dir is not None:
            user_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(user_dir),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CliResult(-1, "", f"Timed out after {int(timeout)} s", timed_out=True)
        return CliResult(
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )


# --------------------------------------------------------------------------
# JSON shape helpers
# --------------------------------------------------------------------------

def _as_list(data: Any, *keys: str) -> list:
    """Returns data[key] for the first key present, or data if it is a list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def parse_installed_boards(data: Any) -> list[dict]:
    """`board listall --format json` -> app Board objects."""
    boards = []
    for b in _as_list(data, "boards"):
        if not isinstance(b, dict):
            continue
        platform = b.get("platform") or {}
        meta = platform.get("metadata") or {}
        release = platform.get("release") or {}
        core = meta.get("id") or platform.get("id") or ":".join(b.get("fqbn", "").split(":")[:2])
        version = release.get("version") or platform.get("installed") or ""
        boards.append({
            "name": b.get("name", "Unknown"),
            "fqbn": b.get("fqbn", ""),
            "core": core,
            "version": version,
            "installed": True,
        })
    return boards


def parse_core_search(data: Any, query: str = "") -> list[dict]:
    """`core search --format json` -> one Board entry per board of each platform."""
    q = query.lower()
    out = []
    for p in _as_list(data, "platforms"):
        if not isinstance(p, dict):
            continue
        meta = p.get("metadata") or {}
        core = meta.get("id") or p.get("id") or ""
        installed_version = p.get("installed_version") or p.get("installed") or ""
        latest = p.get("latest_version") or p.get("latest") or ""
        releases = p.get("releases") or {}
        release = releases.get(latest) if isinstance(releases, dict) else None
        name = (release or {}).get("name") or p.get("name") or core
        boards = (release or {}).get("boards") or p.get("boards") or []
        if not boards:
            boards = [{"name": name}]
        platform_matches = q in core.lower() or q in name.lower()
        for b in boards:
            bname = b.get("name", name)
            if q and not platform_matches and q not in bname.lower():
                continue
            out.append({
                "name": bname,
                "fqbn": b.get("fqbn", ""),
                "core": core,
                "version": latest,
                "installed": bool(installed_version),
            })
    return out


def _library_fields(lib: dict, release: dict | None = None) -> dict:
    src = release or lib
    return {
        "name": lib.get("name", ""),
        "author": src.get("author", ""),
        "version": src.get("version", ""),
        "sentence": src.get("sentence", ""),
        "paragraph": src.get("paragraph", ""),
        "url": src.get("website", "") or src.get("url", ""),
        "category": src.get("category", ""),
        "architectures": list(src.get("architectures") or []),
    }


def parse_installed_libraries(data: Any) -> list[dict]:
    out = []
    for item in _as_list(data, "installed_libraries"):
        lib = item.get("library", item) if isinstance(item, dict) else None
        if isinstance(lib, dict):
            entry = _library_fields(lib)
            entry["installed"] = True
            out.append(entry)
    return out


def parse_library_search(data: Any) -> list[dict]:
    out = []
    for lib in _as_list(data, "libraries"):
        if not isinstance(lib, dict):
            continue
        latest = lib.get("latest")
        if not isinstance(latest, dict):
            releases = lib.get("releases") or {}
            if isinstance(releases, dict) and releases:
                latest = releases[sorted(releases, key=_version_key)[-1]]
        entry = _library_fields(lib, latest if isinstance(latest, dict) else None)
        entry["available_versions"] = library_versions(lib)
        out.append(entry)
    return out


def library_versions(lib: dict) -> list[str]:
    versions = lib.get("available_versions")
    if not versions and isinstance(lib.get("releases"), dict):
        versions = list(lib["releases"].keys())
    versions = [v for v in (versions or []) if isinstance(v, str)]
    return sorted(set(versions), key=_version_key, reverse=True)


def _version_key(v: str) -> tuple:
    parts = []
    for piece in v.replace("-", ".").split("."):
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(parts)


def parse_library_examples(data: Any) -> list[dict]:
    """`lib examples --format json` -> [{name, examples: [abs paths]}]."""
    out = []
    for item in _as_list(data, "examples"):
        if not isinstance(item, dict):
            continue
        lib = item.get("library") or {}
        examples = [e for e in (item.get("examples") or []) if isinstance(e, str)]
        if lib.get("name") and examples:
            out.append({"name": lib["name"], "examples": examples})
    return out


# --------------------------------------------------------------------------
# Missing libraries (#include <X.h> with no library installed)
# --------------------------------------------------------------------------

_MISSING_HEADER_RE = re.compile(
    r"fatal error: ([A-Za-z0-9_./+-]+\.(?:h|hpp|hh)): No such file or directory")

#: Headers that belong to a board core. If one is missing the board does not
#: support it, and installing a library with the same name would not help.
CORE_HEADERS = frozenset({
    "Arduino.h", "Wire.h", "SPI.h", "EEPROM.h", "SoftwareSerial.h",
    "HardwareSerial.h", "WiFi.h", "Keyboard.h", "Mouse.h", "HID.h",
})

#: Header -> library name for popular libraries whose names differ from their
#: header. Anything else is looked up with `lib search provides:<header>`.
HEADER_LIBRARIES = {
    "Servo.h": "Servo",
    "Stepper.h": "Stepper",
    "Ethernet.h": "Ethernet",
    "SD.h": "SD",
    "WiFiNINA.h": "WiFiNINA",
    "LiquidCrystal.h": "LiquidCrystal",
    "LiquidCrystal_I2C.h": "LiquidCrystal I2C",
    "DHT.h": "DHT sensor library",
    "DHT_U.h": "DHT sensor library",
    "OneWire.h": "OneWire",
    "DallasTemperature.h": "DallasTemperature",
    "Adafruit_NeoPixel.h": "Adafruit NeoPixel",
    "Adafruit_GFX.h": "Adafruit GFX Library",
    "Adafruit_SSD1306.h": "Adafruit SSD1306",
    "Adafruit_Sensor.h": "Adafruit Unified Sensor",
    "Adafruit_BMP280.h": "Adafruit BMP280 Library",
    "Adafruit_BME280.h": "Adafruit BME280 Library",
    "Adafruit_MPU6050.h": "Adafruit MPU6050",
    "IRremote.h": "IRremote",
    "IRremote.hpp": "IRremote",
    "FastLED.h": "FastLED",
    "NewPing.h": "NewPing",
    "RF24.h": "RF24",
    "ArduinoJson.h": "ArduinoJson",
    "PubSubClient.h": "PubSubClient",
    "U8g2lib.h": "U8g2",
    "TM1637Display.h": "TM1637",
    "LedControl.h": "LedControl",
    "TMRpcm.h": "TMRpcm",
}


def missing_headers(logs: str) -> list[str]:
    """Headers the compiler could not find, in order, without duplicates."""
    seen: list[str] = []
    for m in _MISSING_HEADER_RE.finditer(logs):
        header = m.group(1)
        if header not in seen:
            seen.append(header)
    return seen


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("library", ""))


def pick_library(header: str, search_data: Any) -> str | None:
    """Chooses a library from `lib search provides:<header>` output: the one
    whose name matches the header, otherwise the first result."""
    libs = [l for l in _as_list(search_data, "libraries")
            if isinstance(l, dict) and l.get("name")]
    if not libs:
        return None
    base = _norm(header.rsplit(".", 1)[0])
    for lib in libs:
        if _norm(lib["name"]) == base:
            return lib["name"]
    return libs[0]["name"]
