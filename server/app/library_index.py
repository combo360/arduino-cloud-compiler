"""Read-only access to the Arduino library index (archive URLs).

Why this module exists
----------------------
The mobile app installs libraries **on the phone**. It therefore never needs the
server to run ``arduino-cli lib install`` (which mutates shared state and needs
an API key); it only needs the *archive bytes* of a library release.

The archive URL for a release is not in ``arduino-cli lib search`` output - it
lives in the Arduino library index, which arduino-cli caches next to its data
directory as ``library_index.json``. This module reads that cache, keeps the
parsed form in memory with a TTL, and answers ``name[, version] -> url``.

Nothing here writes to the core or library folders: the index is a cache, and
resolving a URL has no side effects beyond reading it.
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import time
from pathlib import Path

from . import cli as clib

log = logging.getLogger("idroid.library_index")

#: How long a parsed index stays valid. The index itself changes rarely; ten
#: minutes keeps repeat downloads cheap without serving stale entries for long.
DEFAULT_TTL_S = 600

#: How long a lazy refresh may take. Deliberately shorter than any client's
#: patience and *never* on the startup path: a missing index must not delay the
#: health check (that mistake took the service down once already).
REFRESH_TIMEOUT_S = 90

#: Do not retry a failed refresh more often than this, so a broken CLI or a
#: flaky network cannot turn every download into a 90s wait.
REFRESH_RETRY_S = 600

#: Candidate locations, relative to the arduino-cli data directory. Newer
#: arduino-cli versions keep the index at the root; some layouts nest it.
_INDEX_CANDIDATES = (
    "library_index.json",
    "libraries/library_index.json",
    "inventory/library_index.json",
)


class LibraryIndexUnavailable(RuntimeError):
    """The index file is missing or unreadable; the server cannot resolve URLs."""


def _version_key(version: str) -> tuple:
    """Sortable key, matching app.cli so "latest" agrees with the search API."""
    return clib._version_key(version)  # noqa: SLF001 - same package, same rule


def find_index_file(data_dir: Path) -> Path | None:
    for relative in _INDEX_CANDIDATES:
        candidate = data_dir / relative
        if candidate.is_file():
            return candidate
    return None


def _release_url(entry: object) -> str:
    """Archive URL from a release entry: `url`, or the first `resources[].url`."""
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    url = str(entry.get("url") or "").strip()
    if url:
        return url
    for resource in entry.get("resources") or []:
        if isinstance(resource, dict) and resource.get("url"):
            return str(resource["url"]).strip()
    return ""


def parse_index(data: object) -> dict[str, dict]:
    """``library_index.json`` -> {casefolded name: {"name", "releases": {ver: url}}}.

    Two shapes exist in the wild and both are handled:

    * **flat** - the file published at downloads.arduino.cc has one entry *per
      release* (``{"name": ..., "version": ..., "url": ...}`` repeated ~55k
      times), so releases must be grouped by name;
    * **nested** - some cached/older revisions nest them under ``releases``.

    The parser is deliberately tolerant: a single release without a URL must not
    discard the rest of the index.
    """
    libraries = data.get("libraries") if isinstance(data, dict) else None
    parsed: dict[str, dict] = {}
    for entry in libraries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        releases: dict[str, str] = {}
        raw_releases = entry.get("releases")
        if isinstance(raw_releases, dict):
            for version, release in raw_releases.items():
                url = _release_url(release)
                if url:
                    releases[str(version).strip()] = url
        else:
            version = str(entry.get("version") or "").strip()
            url = _release_url(entry)
            if version and url:
                releases[version] = url
        if not releases:
            continue
        record = parsed.setdefault(name.casefold(), {"name": name, "releases": {}})
        record["releases"].update(releases)
    return parsed

class LibraryIndex:
    """Resolves library archive URLs from the cached Arduino index."""

    def __init__(self, data_dir: Path, ttl_seconds: int = DEFAULT_TTL_S) -> None:
        self._data_dir = Path(data_dir)
        self._ttl = ttl_seconds
        self._entries: dict[str, dict] | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()
        #: Number of times the index file was read; asserted by tests.
        self.loads = 0
        #: Set by tests to bypass the TTL.
        self.force_reload = False
        #: Lazy refresh bookkeeping: at most one attempt per REFRESH_RETRY_S.
        self._refresh_attempted_at = 0.0
        self._refresh_lock = asyncio.Lock()
        #: Set by tests to keep them offline and fast.
        self.allow_refresh = True

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _is_fresh(self) -> bool:
        return (
            self._entries is not None
            and not self.force_reload
            and (time.monotonic() - self._loaded_at) < self._ttl
        )

    async def _entries_for_request(self) -> dict[str, dict]:
        if self._is_fresh():
            return self._entries or {}
        async with self._lock:
            if self._is_fresh():  # another request loaded it while we waited
                return self._entries or {}
            path = find_index_file(self._data_dir)
            if path is None:
                await self._refresh_once()
                path = find_index_file(self._data_dir)
            if path is None:
                raise LibraryIndexUnavailable(
                    f"No library index at {self._data_dir}. "
                    "Run `arduino-cli lib update-index` once."
                )
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise LibraryIndexUnavailable(
                    f"Cannot read the library index at {path}: {error}"
                ) from error
            self._entries = parse_index(raw)
            self._loaded_at = time.monotonic()
            self.loads += 1
            log.info("library index loaded: %d libraries from %s",
                     len(self._entries), path)
            return self._entries

    async def _refresh_once(self) -> None:
        """Fetch the index once, bounded, and never on the startup path.

        A hosted data disk that already exists skips the image's seeding, so the
        index can legitimately be absent on a live instance. Fetching it here
        means only the first download request pays the cost, the timeout caps it,
        and a failure is a warning plus the existing typed 503 - never a crash
        and never a delayed health check.
        """
        if not self.allow_refresh:
            return
        now = time.monotonic()
        if self._refresh_attempted_at and (now - self._refresh_attempted_at) < REFRESH_RETRY_S:
            return
        async with self._refresh_lock:
            if self._refresh_attempted_at and (time.monotonic() - self._refresh_attempted_at) < REFRESH_RETRY_S:
                return
            self._refresh_attempted_at = time.monotonic()
            env = dict(os.environ)
            env["ARDUINO_DIRECTORIES_DATA"] = str(self._data_dir)
            env["ARDUINO_DIRECTORIES_USER"] = str(self._data_dir)
            log.info("library index missing - fetching it (max %ss)", REFRESH_TIMEOUT_S)
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    "arduino-cli", "lib", "update-index",
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                output, _ = await asyncio.wait_for(process.communicate(), REFRESH_TIMEOUT_S)
                if process.returncode != 0:
                    log.warning("library index refresh exited %s: %s",
                                process.returncode, (output or b"")[-400:].decode("utf-8", "replace"))
                else:
                    log.info("library index refreshed")
            except asyncio.TimeoutError:
                log.warning("library index refresh timed out after %ss", REFRESH_TIMEOUT_S)
                if process is not None:
                    try: process.kill()
                    except ProcessLookupError: pass
            except OSError as error:
                log.warning("library index refresh unavailable: %s", error)
    async def archive_url(self, name: str, version: str = "") -> str | None:
        """Archive URL for ``name`` (exact or latest), or None if not found."""
        wanted = (name or "").strip()
        if wanted.casefold().startswith("library:"):
            wanted = wanted.split(":", 1)[1]
        if not wanted:
            return None
        entries = await self._entries_for_request()
        entry = entries.get(wanted.casefold())
        if entry is None:
            return None
        releases: dict[str, str] = entry["releases"]
        if version:
            return releases.get(version)
        # Latest by the same ordering the search API uses.
        latest = sorted(releases, key=_version_key)[-1]
        return releases[latest]

    async def versions(self, name: str) -> list[str]:
        entries = await self._entries_for_request()
        entry = entries.get((name or "").strip().casefold())
        if entry is None:
            return []
        return sorted(entry["releases"], key=_version_key, reverse=True)
