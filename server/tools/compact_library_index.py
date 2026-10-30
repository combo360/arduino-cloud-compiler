"""Compact arduino-cli's library_index.json into a small lookup file.

Why: the published index is ~57 MB of JSON and expands to a much larger Python
object graph. Parsing it inside a 512 MB container gets the worker OOM-killed
(observed as a 502 on the download route while /health stays 200). Compacting at
image-build time turns it into a few MB that load instantly and cheaply.

Output shape matches what parse_index already reads: {"libraries": [{"name", "releases": {ver: url}}]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def compact(source: Path, target: Path) -> tuple[int, int]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    libraries = raw.get("libraries") if isinstance(raw, dict) else None
    out: dict[str, dict] = {}
    for entry in libraries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        releases = entry.get("releases")
        pairs: list[tuple[str, str]] = []
        if isinstance(releases, dict):
            for version, release in releases.items():
                url = release if isinstance(release, str) else (release or {}).get("url") or ""
                for resource in (release or {}).get("resources") or [] if isinstance(release, dict) else []:
                    url = url or (resource or {}).get("url") or ""
                    if url:
                        break
                if version and url:
                    pairs.append((str(version).strip(), str(url).strip()))
        else:
            version = str(entry.get("version") or "").strip()
            url = str(entry.get("url") or "").strip()
            if version and url:
                pairs.append((version, url))
        if not pairs:
            continue
        record = out.setdefault(name.casefold(), {"n": name, "r": {}})
        record["r"].update(dict(pairs))
    payload = {"libraries": [{"name": record["n"], "releases": record["r"]} for record in out.values()]}
    target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return len(out), target.stat().st_size


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "library_index.json")
    target = Path(sys.argv[2] if len(sys.argv) > 2 else "library_urls.json")
    libraries, size = compact(source, target)
    print(f"compacted {libraries} libraries -> {target} ({size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())