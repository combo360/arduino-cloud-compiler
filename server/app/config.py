"""Server configuration, read from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def _parse_keys(raw: str) -> dict[str, str]:
    """Parses "key1:alice,key2:bob" (or bare "key1") into {key: user}."""
    keys: dict[str, str] = {}
    # Tolerate values pasted with surrounding quotes, or one key per line.
    raw = raw.strip().strip('"').strip("'")
    for item in raw.replace("\n", ",").split(","):
        item = item.strip().strip('"').strip("'").strip()
        if not item:
            continue
        key, _, user = item.partition(":")
        key = key.strip()
        user = (user.strip() or f"user{len(keys) + 1}")
        if key:
            keys[key] = user
    return keys


@dataclass
class Settings:
    #: API keys for normal users: {key: user id}. Each user gets their own
    #: library folder.
    api_keys: dict[str, str] = field(default_factory=dict)
    #: API keys allowed to install/remove board cores and change board URLs.
    admin_keys: dict[str, str] = field(default_factory=dict)
    #: Accept requests without a key (shared "anonymous" user, read-only for
    #: boards). Off by default.
    allow_anonymous: bool = False

    arduino_cli: str | list[str] = "arduino-cli"
    clang_format: str = "clang-format"
    #: Arduino's built-in examples (a checkout of github.com/arduino/arduino-examples).
    builtin_examples_dir: Path | None = None
    data_dir: Path = Path("/data")
    #: Board index URLs used until an admin changes the list.
    default_board_urls: list[str] = field(default_factory=list)

    #: When a sketch includes a header that no installed library provides,
    #: install the matching library for that user and compile again.
    auto_install_libraries: bool = True

    compile_timeout_s: int = 300
    install_timeout_s: int = 600
    max_concurrent_compiles: int = 2
    max_sketch_bytes: int = 256 * 1024
    max_files: int = 20

    #: Sliding one-minute windows per API key.
    compiles_per_minute: int = 10
    requests_per_minute: int = 120

    #: Online assistant. All three LLM values must be set for it to turn on;
    #: the provider key never leaves the server.
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    #: Lets the assistant be switched off without deleting the keys.
    assistant_enabled: bool = True
    #: Chat answers cost money per message, so they get their own low limit.
    assistant_per_minute: int = 6
    #: Lets callers without an API key use the assistant too. Off by default:
    #: an answer costs money, so turning this on means anyone who installs the
    #: app can spend the provider balance, bounded by [assistant_per_minute]
    #: and the per-IP bucket that anonymous callers already share.
    assistant_allow_anonymous: bool = False
    #: Pre-approved words ("word:label") that work without requesting a key.
    assistant_words: dict[str, str] = field(default_factory=dict)
    #: Generated keys expire this many days after they are minted.
    assistant_key_days: int = 30
    #: One key request per IP per this many seconds, so the operator's inbox
    #: cannot be used as a spam target by a public app.
    assistant_key_cooldown_s: int = 600
    #: Hard ceiling on answers per day across every caller. The only thing that
    #: makes an open assistant financially safe.
    assistant_daily_cap: int = 300

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def shared_libraries_dir(self) -> Path:
        """Libraries available to everyone (baked into the Docker image)."""
        return self.data_dir / "shared" / "libraries"

    @property
    def board_urls_file(self) -> Path:
        return self.data_dir / "board_urls.json"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            api_keys=_parse_keys(env("IDROID_API_KEYS", "")),
            admin_keys=_parse_keys(env("IDROID_ADMIN_KEYS", "")),
            allow_anonymous=env("IDROID_ALLOW_ANONYMOUS", "false").lower()
            in ("1", "true", "yes"),
            arduino_cli=env("ARDUINO_CLI", "arduino-cli"),
            clang_format=env("CLANG_FORMAT", "clang-format"),
            builtin_examples_dir=Path(env("IDROID_BUILTIN_EXAMPLES", "/opt/arduino-examples/examples")),
            data_dir=Path(env("IDROID_DATA_DIR", "/data")),
            default_board_urls=[u for u in re.split(r"[\s,]+", env("IDROID_DEFAULT_BOARD_URLS", ""))
                                if u],
            auto_install_libraries=env("IDROID_AUTO_INSTALL_LIBRARIES", "true").lower()
            in ("1", "true", "yes"),
            compile_timeout_s=int(env("IDROID_COMPILE_TIMEOUT", "300")),
            install_timeout_s=int(env("IDROID_INSTALL_TIMEOUT", "600")),
            max_concurrent_compiles=int(env("IDROID_MAX_CONCURRENT_COMPILES", "2")),
            max_sketch_bytes=int(env("IDROID_MAX_SKETCH_BYTES", str(256 * 1024))),
            compiles_per_minute=int(env("IDROID_COMPILES_PER_MINUTE", "10")),
            requests_per_minute=int(env("IDROID_REQUESTS_PER_MINUTE", "120")),
            llm_base_url=env("IDROID_LLM_BASE_URL", "").strip(),
            llm_api_key=env("IDROID_LLM_API_KEY", "").strip(),
            llm_model=env("IDROID_LLM_MODEL", "").strip(),
            assistant_enabled=env("IDROID_ASSISTANT_ENABLED", "true").lower()
            in ("1", "true", "yes"),
            assistant_per_minute=int(env("IDROID_ASSISTANT_PER_MINUTE", "6")),
            assistant_allow_anonymous=env(
                "IDROID_ASSISTANT_ALLOW_ANONYMOUS", "false").lower()
            in ("1", "true", "yes"),
            assistant_words=_parse_keys(env("IDROID_ASSISTANT_WORDS", "")),
            assistant_key_days=int(env("IDROID_ASSISTANT_KEY_DAYS", "30")),
            assistant_key_cooldown_s=int(
                env("IDROID_ASSISTANT_KEY_COOLDOWN", "600")),
            assistant_daily_cap=int(env("IDROID_ASSISTANT_DAILY_CAP", "300")),
        )
