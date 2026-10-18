# i-Droid compile server

A small HTTP wrapper around [arduino-cli](https://arduino.github.io/arduino-cli/)
that the i-Droid app uses to compile sketches and manage boards and libraries.
It replaces the original shared Render service with one you control.

## Compared with the original server

The original server's code (`docs/original_server/`) was reviewed in October
2026. This server keeps its useful behaviour and fixes its problems.

| Concern | Original server | This server |
|---|---|---|
| Access | Open to anyone; CORS `*` | API key per user (`X-API-Key`); optional anonymous mode |
| Board cores and board URLs | Anyone could install or remove them for everyone | Admin keys only |
| Libraries | One shared folder; anyone could install or remove | Per-user folder, plus shared libraries baked into the image (read-only) |
| `#include` of a missing library | Installed automatically, from a fixed name table, for everyone | Installed automatically **for that user only**, only when the compiler reports the header missing; name table plus `lib search provides:<header>`; every install is shown in the log |
| Preinstalled libraries | 55, failures hidden (`\|\| true`) | 26 common ones (`preinstall-libraries.txt`), failures shown in the build log |
| Speed | Persistent build folder per board; core cache warmed at build | Persistent core build cache on `/data`, warmed at build |
| Concurrency | Compiles ran synchronously, so one compile blocked every other request; compiles for the same board shared one build folder | Async subprocesses, at most 2 compiles at once, separate build folder per compile |
| Abuse | No limits | Per-key rate limits, timeouts, 256 KB sketch limit |
| Sketches | Single file | Multi-file sketches (`.ino` + `.h/.hpp/.c/.cpp/.S`) |
| Output | Reports failure unless a `.hex` is produced, so ESP32 builds (which produce `.bin`) are reported as failed | `hex` (AVR) or `binary` (ESP32 etc.) |
| Input checks | None (no shell used, so no command injection) | Board ids, core ids, URLs, library names and file names validated |
| Auto Format, Examples | No | `POST /v1/format`, `GET /v1/examples` |
| arduino-cli | 0.35.3 (Feb 2024) | 1.2.2 |

The app works with both servers: request and response fields are compatible,
and DELETE requests send their parameters in the query string, which both
servers accept.

## Run with Docker

```bash
cd server
docker build -t idroid-server .
docker run -p 8080:8080 -v idroid-data:/data \
  -e IDROID_API_KEYS="alice-key:alice,bob-key:bob" \
  -e IDROID_ADMIN_KEYS="a-long-random-admin-key:admin" \
  idroid-server
```

Then in the app: **Settings → Server URL** `http://<your-pc-ip>:8080`,
**API key** `alice-key`, and tap **Test connection**.
Use HTTPS (a reverse proxy or a hosting platform) for anything beyond your
own network: the API key and sketches travel in the request.

Generate keys with `python -c "import secrets; print(secrets.token_urlsafe(24))"`.

### Build options

| Build argument | Default | Purpose |
|---|---|---|
| `PREINSTALL_CORES` | `arduino:avr` | Cores baked into the image, e.g. `"arduino:avr arduino:samd esp32:esp32"` |
| `PREINSTALL_URLS` | (none) | Extra board index URLs for those cores, e.g. `https://espressif.github.io/arduino-esp32/package_esp32_index.json` |

Shared libraries are listed in `preinstall-libraries.txt`, one per line.
Cores, shared libraries and the warmed build cache are copied to `/data` on
first start. To update them on an existing volume, delete `/data/arduino15`,
`/data/shared` or `/data/build-cache` and restart. The ESP32 core adds well
over 1 GB to the image; also add its URL in the app (Boards Manager → URLs)
so it can be updated later.

## Deploy on Render

`render.yaml` (at the repository root) is a Blueprint for a Docker web service with a 5 GB disk at
`/data`. Set `IDROID_API_KEYS` and `IDROID_ADMIN_KEYS` in the dashboard.
A disk needs a paid instance; on the free plan the service sleeps and
installed cores and libraries are lost on each restart.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `IDROID_API_KEYS` | (none) | `key:user,key:user` — normal users |
| `IDROID_ADMIN_KEYS` | (none) | `key:user` — may install/remove cores and board URLs |
| `IDROID_ALLOW_ANONYMOUS` | `false` | Accept requests without a key (no installs) |
| `IDROID_DATA_DIR` | `/data` | Cores, per-user libraries, board URL list |
| `IDROID_COMPILE_TIMEOUT` | `120` | Seconds |
| `IDROID_INSTALL_TIMEOUT` | `600` | Seconds for core/library installs |
| `IDROID_MAX_CONCURRENT_COMPILES` | `2` | Parallel compiles |
| `IDROID_MAX_SKETCH_BYTES` | `262144` | Total size of all files |
| `IDROID_COMPILES_PER_MINUTE` | `10` | Per key |
| `IDROID_REQUESTS_PER_MINUTE` | `120` | Per key, all endpoints |
| `IDROID_AUTO_INSTALL_LIBRARIES` | `true` | Install the library for a missing `#include` (for the calling user) and compile again |
| `ARDUINO_CLI` | `arduino-cli` | Path to the binary |
| `CLANG_FORMAT` | `clang-format` | Used by Auto Format |
| `IDROID_BUILTIN_EXAMPLES` | `/opt/arduino-examples/examples` | Arduino's built-in examples (the Docker image downloads them) |

## API

All endpoints except `/health` need `X-API-Key`. Errors return
`{"detail": "..."}` with 400, 401, 403, 413, 429 or 502.

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/health` | | `{"status":"ok"}` |
| POST | `/v1/compile` | `{sketch, main?, files?: [{name, content}], fqbn, verbose?}` | `{success, logs, fqbn, hex? \| binary?, format}` |
| POST | `/v1/format` | `{code}` | `{formatted}` (Arduino's ClangFormat style) |
| GET | `/v1/examples` | `?fqbn=` | `{groups: [{name, source: builtin\|library, examples: [{id, name}]}]}` |
| GET | `/v1/examples/get` | `?id=` | `{name, files: [{name, content}]}` |
| GET | `/v1/boards/installed` | | `[Board]` |
| GET | `/v1/boards/search` | `?query=` | `[Board]` |
| POST / DELETE | `/v1/boards/install`, `/v1/boards/uninstall` | `{core}`; DELETE also `?core=` (admin) | `{success, logs}` |
| GET / POST / DELETE | `/v1/boards/urls` | `{url}`; DELETE also `?url=` (admin for changes) | `{urls}` / `{success, urls}` |
| GET | `/v1/libraries/installed` | | `[Library]` (caller's own) |
| GET | `/v1/libraries/search` | `?query=` | `[Library]` |
| GET | `/v1/libraries/versions` | `?name=` | `{versions}` |
| POST / DELETE | `/v1/libraries/install`, `/v1/libraries/uninstall` | `{name, version?}`; DELETE also `?name=` | `{success, logs}` |

`Board` = `{name, fqbn, core, version, installed}`;
`Library` = `{name, author, version, sentence, paragraph, url, category, architectures}`.

## Tests

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The tests use a fake `arduino-cli` (`tests/fake_arduino_cli.py`), so they run
without Arduino tools or internet access. They cover authentication, admin-only
core changes, per-user libraries, rate limits, input validation, multi-file
sketches, HEX vs binary output, verbose compiles, automatic installs for
missing `#include`s, shared libraries, query-string DELETEs, Auto Format (with the real
clang-format when installed), examples including path-traversal checks, and the
JSON shapes the app expects.

## Known limits

- Rate limits are kept in memory: they reset on restart and are per process
  (run one worker, or put a shared limiter in front).
- Board cores are shared by all users by design (they are large).
- The build runs the arduino-cli toolchain on code you receive. Keep the
  container unprivileged (the image runs as a non-root user) and give API
  keys only to people you trust.
