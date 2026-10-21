"""Access keys for the online assistant.

A key is generated on request and emailed to the operator, who hands it to the
people who need it. Only a hash is stored, so the file on disk cannot be used to
recover a key, and a key expires on its own.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

# No 0/o/1/l/i: these keys get read off a screen and typed by hand.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class KeyStore:
    def __init__(self, path: Path, ttl_days: int = 30) -> None:
        self._path = path
        self._ttl_s = ttl_days * 86400

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        keys = data.get("keys") if isinstance(data, dict) else None
        return [k for k in keys if isinstance(k, dict)] if isinstance(keys, list) else []

    def _save(self, keys: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"keys": keys}, indent=2))

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.strip().lower().encode()).hexdigest()

    @staticmethod
    def _live(entry: dict) -> bool:
        return int(entry.get("expires", 0)) > time.time()

    def create(self, label: str = "") -> str:
        """Mints a key, stores its hash, and returns the key itself."""
        raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
        key = f"idroid-{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        # Drop expired entries while we are writing anyway.
        keys = [entry for entry in self._load() if self._live(entry)]
        keys.append({
            "hash": self._hash(key),
            "label": label[:80],
            "created": int(time.time()),
            "expires": int(time.time()) + self._ttl_s,
        })
        self._save(keys)
        return key

    def accepts(self, key: str) -> bool:
        if not key or not key.strip():
            return False
        wanted = self._hash(key)
        return any(entry.get("hash") == wanted and self._live(entry)
                   for entry in self._load())

    def live_count(self) -> int:
        return len([entry for entry in self._load() if self._live(entry)])
