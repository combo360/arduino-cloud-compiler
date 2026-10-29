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
| Concurrency | Compiles ran synchronously, so one compile blocked every other request; compiles for the same board shared one build folder | Async subprocesses, a configurable number of compiles at once, separate build folder per compile |
| Abuse | No limits | Per-key rate limits, timeouts, 256 KB sketch limit |
| Sketches | Single file | Multi-file sketches (`.ino` + `.h/.hpp/.c/.cpp/.S`) |
| Output | Reports failure unless a `.hex` is produced, so ESP32 builds (which produce `.bin`) are reported as failed | `hex` (AVR) or `binary` (ESP32 etc.); for ESP32 also every flash image with its offset, so the app can upload from the phone |
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
docker run -p 8090:8080 -v idroid-data:/data \
  -e IDROID_API_KEYS="alice-key:alice,bob-key:bob" \
  -e IDROID_ADMIN_KEYS="a-long-random-admin-key:admin" \
  idroid-server
```

The container listens on 8080 but is published on **8090** so it does not
collide with whatever else is already using 8080 on your machine (a PHP or
Node dev server, for example).

Then in the app: **Settings → Server URL** `http://<your-pc-ip>:8090`,
**API key** `alice-key`, and tap **Test connection**. It asks `/health`, which
needs no key, and then `/v1/boards/urls`, which does — so a wrong key is
reported there (`Server refused the request (401)`) instead of at the first
compile.
Use HTTPS (a reverse proxy or a hosting platform) for anything beyond your
own network: the API key and sketches travel in the request.

Generate keys with `python -c "import secrets; print(secrets.token_urlsafe(24))"`.

### Build options

| Build argument | Default | Purpose |
|---|---|---|
| `PREINSTALL_CORES` | `arduino:avr esp32:esp32` | Cores baked into the image |
| `PREINSTALL_URLS` | the ESP32 board index | Board index URLs for those cores; also the server's starting list of board URLs |

The ESP32 core makes the image well over 1 GB larger and the first build
about 10–20 minutes longer. For an AVR-only image:

```bash
docker build --build-arg PREINSTALL_CORES=arduino:avr --build-arg PREINSTALL_URLS= -t idroid-server .
```

Shared libraries are listed in `preinstall-libraries.txt`, one per line.
Cores, shared libraries and the warmed build cache are copied to `/data` on
first start. To update them on an existing volume, delete `/data/arduino15`,
`/data/shared` or `/data/build-cache` and restart.

### ESP32 builds

For ESP32 boards `/v1/compile` returns, besides `binary` (the sketch), the
flash settings and every image the board needs, read from the `flash_args`
file that the arduino-esp32 core (3.x) writes:

```json
"flash": {"mode": "dio", "freq": "80m", "size": "4MB"},
"images": [
  {"offset": 4096,  "name": "sketch.ino.bootloader.bin", "data": "<base64>"},
  {"offset": 32768, "name": "sketch.ino.partitions.bin", "data": "<base64>"},
  {"offset": 57344, "name": "boot_app0.bin",             "data": "<base64>"},
  {"offset": 65536, "name": "sketch.ino.bin",            "data": "<base64>"}
]
```

The app writes these images at their offsets (the same files and offsets the
Arduino IDE uses). ESP32 compiles need much more memory than AVR ones; run one
compile at a time on a small instance (`IDROID_MAX_CONCURRENT_COMPILES=1`).

## Deploy on Render

`render.yaml` (at the repository root) is a Blueprint for a Docker web service with a 5 GB disk at
`/data`. Set `IDROID_API_KEYS` and `IDROID_ADMIN_KEYS` in the dashboard.
A disk needs a paid instance; on the free plan the service sleeps and
installed cores and libraries are lost on each restart.

## Classroom use (no API key for students)

For a class the app should work as soon as it is installed, without handing a
key to every student. Set `IDROID_ALLOW_ANONYMOUS=true`: callers without a key
are then accepted as the shared `anonymous` user.

| Can do without a key | Needs a key |
|---|---|
| Compile, Auto Format, Examples, list/search boards | Install or remove board cores (admin key) |
| List/search libraries, read the board URL list | Install or remove libraries, change board URLs |

Anonymous callers are rate-limited **per IP** (`anon:<host>`), so a classroom
behind one router shares a single bucket — raise
`IDROID_COMPILES_PER_MINUTE` / `IDROID_REQUESTS_PER_MINUTE` accordingly
(`render.yaml` sets 30 and 300). Anonymous callers cannot install libraries,
so anything the course includes must be baked into the image
(`preinstall-libraries.txt`), and the cores students need must be installed
once: either baked at build time (`PREINSTALL_CORES`, the default already
includes `esp32:esp32`) or installed with the admin key.

Only the administrator installs cores: they are shared by every user, so one
student changing them would affect the whole class.

Two infrastructure traps, both seen on the live service:

- **No disk → everything installed at runtime is lost.** Cores installed with
  the admin key, per-user libraries and the board URL list live under `/data`.
  Without a disk mounted there, a deploy, a crash or an out-of-memory kill
  resets the server to what the image contains. Check `render.yaml`: the
  service needs `disk.mountPath: /data`. With the disk, the image's cores,
  shared libraries and build cache are seeded once on first start.
- **ESP32 compiles need memory.** On a 512 MB instance an ESP32 compile is
  killed and the service returns 503 for a while; use ~2 GB. The first ESP32
  compile after a deploy also takes minutes, so keep
  `IDROID_COMPILE_TIMEOUT=300`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `IDROID_API_KEYS` | (none) | `key:user,key:user` — normal users |
| `IDROID_ADMIN_KEYS` | (none) | `key:user` — may install/remove cores and board URLs |
| `IDROID_ALLOW_ANONYMOUS` | `false` | Accept requests without a key (no installs) |
| `IDROID_DATA_DIR` | `/data` | Cores, per-user libraries, board URL list |
| `IDROID_COMPILE_TIMEOUT` | `300` | Seconds (ESP32 builds can take minutes when the core is not cached) |
| `IDROID_INSTALL_TIMEOUT` | `600` | Seconds for core/library installs |
| `IDROID_MAX_CONCURRENT_COMPILES` | `2` | Parallel compiles (`render.yaml` sets 1) |
| `IDROID_MAX_SKETCH_BYTES` | `262144` | Total size of all files |
| `IDROID_COMPILES_PER_MINUTE` | `10` | Per key |
| `IDROID_REQUESTS_PER_MINUTE` | `120` | Per key, all endpoints |
| `IDROID_DEFAULT_BOARD_URLS` | set by the image | Board index URLs used until an admin changes the list |
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
| POST | `/v1/compile` | `{sketch, main?, files?: [{name, content}], fqbn, verbose?}` | `{success, logs, fqbn, hex? \| binary?, format, flash?, images?}` |
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
| GET | `/v1/libraries/download` | `?name=` (optional `&version=`) | **302** to the archive URL - keyless, read-only |
| POST / DELETE | `/v1/libraries/install`, `/v1/libraries/uninstall` | `{name, version?}`; DELETE also `?name=` | `{success, logs}` |

`Board` = `{name, fqbn, core, version, installed}`;
`Library` = `{name, author, version, sentence, paragraph, url, category, architectures}`.

### Keyless library downloads (for on-device installs)

`GET /v1/libraries/download?name=DHT%20sensor%20library[&version=1.4.6]`

The app installs libraries **on the phone**, so it needs the archive bytes, not a
server-side install. This endpoint resolves a library release to its archive URL
and answers with a `302` redirect to `downloads.arduino.cc` - no bandwidth
through Render, no shared state touched.

* **No API key** (same policy as compile/browse, i.e. when
  `IDROID_ALLOW_ANONYMOUS=true`; with anonymous access disabled it requires a key
  like every other endpoint).
* **Read-only**: it never runs `arduino-cli lib install` and never writes into
  the core or library folders. `POST /v1/libraries/install` stays keyed because
  it *does* mutate shared state.
* Resolved from arduino-cli's cached `library_index.json`, parsed once and kept
  in memory for 10 minutes.
* `404` with a clear `detail` for an unknown library or version; `503` if the
  index has not been downloaded yet (run `arduino-cli lib update-index`).

```bash
curl -i "http://127.0.0.1:8090/v1/libraries/download?name=TM1637"
# HTTP/1.1 302 Found
# location: https://downloads.arduino.cc/libraries/github.com/avishorp/TM1637-1.2.0.zip
```

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
