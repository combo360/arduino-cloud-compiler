"""Server configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_keys(raw: str) -> dict[str, str]:
    """Parses "key1:alice,key2:bob" (or bare "key1") into {key: user}."""
    keys: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
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

    arduino_cli: str = "arduino-cli"
    clang_format: str = "clang-format"
    #: Arduino's built-in examples (a checkout of github.com/arduino/arduino-examples).
    builtin_examples_dir: Path | None = None
    data_dir: Path = Path("/data")

    #: When a sketch includes a header that no installed library provides,
    #: install the matching library for that user and compile again.
    auto_install_libraries: bool = True

    compile_timeout_s: int = 120
    install_timeout_s: int = 600
    max_concurrent_compiles: int = 2
    max_sketch_bytes: int = 256 * 1024
    max_files: int = 20

    #: Sliding one-minute windows per API key.
    compiles_per_minute: int = 10
    requests_per_minute: int = 120

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
            auto_install_libraries=env("IDROID_AUTO_INSTALL_LIBRARIES", "true").lower()
            in ("1", "true", "yes"),
            compile_timeout_s=int(env("IDROID_COMPILE_TIMEOUT", "120")),
            install_timeout_s=int(env("IDROID_INSTALL_TIMEOUT", "600")),
            max_concurrent_compiles=int(env("IDROID_MAX_CONCURRENT_COMPILES", "2")),
            max_sketch_bytes=int(env("IDROID_MAX_SKETCH_BYTES", str(256 * 1024))),
            compiles_per_minute=int(env("IDROID_COMPILES_PER_MINUTE", "10")),
            requests_per_minute=int(env("IDROID_REQUESTS_PER_MINUTE", "120")),
        )
