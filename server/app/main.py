"""i-Droid compile server: an arduino-cli wrapper with API keys,
per-user libraries, admin-only board cores and rate limits.

Run locally:  uvicorn --factory app.main:create_app --port 8090
(8090 rather than 8080, which is commonly taken by another dev server.)
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import assistant as asist
from . import cli as clib
from . import mail
from .assistant_keys import KeyStore
from .config import Settings

FQBN_RE = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+(:[A-Za-z0-9_.=,-]+)?$")
CORE_RE = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+(@[A-Za-z0-9_.+-]+)?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,32}$")
LIB_NAME_RE = re.compile(r"^[^\x00-\x1f@]{1,100}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,63}\.(ino|h|hpp|c|cpp|S)$")
USER_RE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class Caller:
    user: str
    is_admin: bool
    key: str


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SketchFile(BaseModel):
    name: str
    content: str


class CompileRequest(BaseModel):
    sketch: str = ""
    main: str | None = None
    files: list[SketchFile] = Field(default_factory=list)
    fqbn: str = "arduino:avr:uno"
    optimize: str | None = None
    verbose: bool = False


class FormatRequest(BaseModel):
    code: str


class CoreRequest(BaseModel):
    core: str


class UrlRequest(BaseModel):
    url: str


class LibraryRequest(BaseModel):
    name: str
    version: str | None = None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding one-minute window per (key, bucket)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, key: str, bucket: str, limit: int) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        q = self._hits[(key, bucket)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            retry = int(60 - (now - q[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit reached ({limit}/min). Try again in {retry} s.",
                headers={"Retry-After": str(retry)},
            )
        q.append(now)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        # The default /data only exists in the container, and on Windows the
        # root of a drive needs administrator rights. Say so instead of dying in
        # a traceback before the first request.
        raise RuntimeError(
            f"Cannot use the data folder '{settings.data_dir}': {error}\n"
            "Set IDROID_DATA_DIR to a folder you can write to, for example "
            f"'{Path.home() / 'idroid-data'}'."
        ) from error
    arduino = clib.ArduinoCli(settings.arduino_cli, settings.data_dir)
    limiter = RateLimiter()
    compile_slots = asyncio.Semaphore(settings.max_concurrent_compiles)
    core_lock = asyncio.Lock()

    app = FastAPI(title="i-Droid compile server", version="1.0.0")
    app.state.settings = settings

    # -- auth ---------------------------------------------------------------

    def caller(request: Request) -> Caller:
        key = request.headers.get("X-API-Key", "").strip()
        if key and key in settings.admin_keys:
            c = Caller(settings.admin_keys[key], True, key)
        elif key and key in settings.api_keys:
            c = Caller(settings.api_keys[key], False, key)
        elif not key and settings.allow_anonymous:
            host = request.client.host if request.client else "anon"
            c = Caller("anonymous", False, f"anon:{host}")
        else:
            raise HTTPException(status_code=401, detail="Missing or invalid API key.")
        limiter.check(c.key, "requests", settings.requests_per_minute)
        return c

    def admin(c: Caller = Depends(caller)) -> Caller:
        if not c.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only an administrator can change board cores and URLs.",
            )
        return c

    def user_dir(c: Caller) -> Path:
        return settings.users_dir / (USER_RE.sub("_", c.user) or "user")

    def board_urls() -> list[str]:
        try:
            return json.loads(settings.board_urls_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return list(settings.default_board_urls)

    def save_board_urls(urls: list[str]) -> None:
        settings.board_urls_file.write_text(json.dumps(urls, indent=2))

    def fail(result: clib.CliResult, what: str) -> HTTPException:
        return HTTPException(status_code=502, detail=f"{what} failed: {result.logs[-2000:]}")

    # -- health -------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # -- compile ------------------------------------------------------------

    user_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run_compile(c: Caller, req: CompileRequest, sketch_dir: Path, out_dir: Path):
        shared = settings.shared_libraries_dir
        return await arduino.run(
            "compile", "--fqbn", req.fqbn, "--output-dir", str(out_dir),
            "--build-path", str(out_dir.with_name("build-path")),
            *(["--libraries", str(shared)] if shared.is_dir() else []),
            *(["-v"] if req.verbose else []),
            str(sketch_dir),
            user_dir=user_dir(c),
            timeout=settings.compile_timeout_s,
        )

    async def install_for_header(c: Caller, header: str) -> tuple[str | None, str]:
        """Finds and installs the library that provides [header]."""
        name = clib.HEADER_LIBRARIES.get(header)
        if name is None:
            r = await arduino.run("lib", "search", f"provides:{header}", "--format", "json",
                                  user_dir=user_dir(c), timeout=180)
            name = clib.pick_library(header, r.json()) if r.ok else None
        if name is None:
            return None, f"No library in the Arduino index provides {header}."
        r = await arduino.run("lib", "install", name, user_dir=user_dir(c),
                              timeout=settings.install_timeout_s)
        if not r.ok:
            return None, f"Installing {name} for {header} failed:\n{r.logs[-1000:]}"
        return name, f"Installed library {name} (provides {header})."

    async def compile_with_libraries(c: Caller, req: CompileRequest,
                                     sketch_dir: Path, out_dir: Path):
        """Compiles; if headers are missing, installs their libraries for this
        user and compiles again (at most twice, for libraries that need
        further libraries)."""
        notes: list[str] = []
        tried: set[str] = set()
        result = await run_compile(c, req, sketch_dir, out_dir)
        for _ in range(2):
            if result.ok:
                break
            headers = [h for h in clib.missing_headers(result.logs)
                       if h not in tried and h not in clib.CORE_HEADERS][:4]
            if not headers:
                break
            if not settings.auto_install_libraries:
                break
            if c.user == "anonymous":
                notes.append("Missing libraries for " + ", ".join(headers) +
                             ". Add an API key in Settings so the server can install them.")
                break
            installed_any = False
            async with user_locks[c.user]:
                for h in headers:
                    tried.add(h)
                    name, note = await install_for_header(c, h)
                    notes.append(note)
                    installed_any = installed_any or name is not None
            if not installed_any:
                break
            shutil.rmtree(out_dir, ignore_errors=True)
            shutil.rmtree(out_dir.with_name("build-path"), ignore_errors=True)
            result = await run_compile(c, req, sketch_dir, out_dir)
        return result, notes

    @app.post("/v1/compile")
    async def compile_sketch(req: CompileRequest, c: Caller = Depends(caller)):
        limiter.check(c.key, "compile", settings.compiles_per_minute)
        if not FQBN_RE.match(req.fqbn):
            raise HTTPException(status_code=400, detail="Invalid board (fqbn).")

        files = list(req.files)
        main_name = req.main or "sketch.ino"
        if not files:
            files = [SketchFile(name=main_name, content=req.sketch)]
        if len(files) > settings.max_files:
            raise HTTPException(status_code=400, detail="Too many files.")
        total = sum(len(f.content.encode()) for f in files)
        if total > settings.max_sketch_bytes:
            raise HTTPException(status_code=413, detail="Sketch is too large.")
        for f in files:
            if not FILE_RE.match(f.name) or "/" in f.name or ".." in f.name:
                raise HTTPException(status_code=400, detail=f"Invalid file name: {f.name!r}")
        if not FILE_RE.match(main_name) or not main_name.endswith(".ino"):
            raise HTTPException(status_code=400, detail="Main file must be a .ino file.")
        if main_name not in {f.name for f in files}:
            files.insert(0, SketchFile(name=main_name, content=req.sketch))

        stem = main_name[:-4].replace(" ", "_")
        async with compile_slots:
            workdir = Path(tempfile.mkdtemp(prefix="idroid-"))
            try:
                sketch_dir = workdir / stem
                out_dir = workdir / "build"
                sketch_dir.mkdir()
                for f in files:
                    name = f"{stem}.ino" if f.name == main_name else f.name
                    (sketch_dir / name).write_text(f.content, encoding="utf-8")

                result, notes = await compile_with_libraries(c, req, sketch_dir, out_dir)
                logs = "\n".join([*notes, result.logs]) if notes else result.logs
                response: dict = {"success": result.ok, "logs": logs, "fqbn": req.fqbn}
                if result.ok:
                    hexes = sorted(
                        (p for p in out_dir.glob("*.hex") if "with_bootloader" not in p.name),
                        key=lambda p: p.name,
                    )
                    bins = sorted(p for p in out_dir.glob("*.bin") if "bootloader" not in p.name)
                    if hexes:
                        response["hex"] = base64.b64encode(hexes[0].read_bytes()).decode()
                        response["format"] = "hex"
                    elif bins:
                        response["binary"] = base64.b64encode(bins[0].read_bytes()).decode()
                        response["format"] = "bin"
                        # ESP32 family: every image the board needs, with its
                        # flash offset, read from the core's flash_args file.
                        flash = clib.parse_flash_args(out_dir.with_name("build-path"))
                        if flash is not None:
                            response["flash"] = flash["settings"]
                            response["images"] = flash["images"]
                    else:
                        response["success"] = False
                        response["logs"] += "\nNo firmware file was produced."
                return response
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

    # -- boards -------------------------------------------------------------

    @app.get("/v1/boards/installed")
    async def boards_installed(c: Caller = Depends(caller)):
        r = await arduino.run("board", "listall", "--format", "json",
                              additional_urls=board_urls())
        if not r.ok:
            raise fail(r, "Listing boards")
        return clib.parse_installed_boards(r.json())

    @app.get("/v1/boards/listall")
    async def boards_listall(c: Caller = Depends(caller)):
        return await boards_installed(c)

    @app.get("/v1/boards/search")
    async def boards_search(query: str = "", c: Caller = Depends(caller)):
        query = query.strip()[:100]
        if not query:
            return []
        r = await arduino.run("core", "search", query, "--format", "json",
                              additional_urls=board_urls())
        if not r.ok:
            raise fail(r, "Board search")
        return clib.parse_core_search(r.json(), query)

    @app.post("/v1/boards/install")
    async def boards_install(req: CoreRequest, c: Caller = Depends(admin)):
        if not CORE_RE.match(req.core):
            raise HTTPException(status_code=400, detail="Invalid core id.")
        async with core_lock:
            urls = board_urls()
            await arduino.run("core", "update-index", additional_urls=urls,
                              timeout=settings.install_timeout_s)
            r = await arduino.run("core", "install", req.core, additional_urls=urls,
                                  timeout=settings.install_timeout_s)
        return {"success": r.ok, "logs": r.logs}

    async def body_field(request: Request, name: str) -> str:
        try:
            data = await request.json()
        except Exception:
            return ""
        value = data.get(name) if isinstance(data, dict) else None
        return value.strip() if isinstance(value, str) else ""

    @app.delete("/v1/boards/uninstall")
    async def boards_uninstall(request: Request, core: str = "",
                               c: Caller = Depends(admin)):
        req = CoreRequest(core=core.strip() or await body_field(request, "core"))
        if not CORE_RE.match(req.core):
            raise HTTPException(status_code=400, detail="Invalid core id.")
        async with core_lock:
            r = await arduino.run("core", "uninstall", req.core,
                                  timeout=settings.install_timeout_s)
        return {"success": r.ok, "logs": r.logs}

    @app.get("/v1/boards/urls")
    async def urls_get(c: Caller = Depends(caller)):
        return {"urls": board_urls()}

    @app.post("/v1/boards/urls")
    async def urls_add(req: UrlRequest, c: Caller = Depends(admin)):
        url = req.url.strip()
        if not re.match(r"^https://[^\s,]+\.json$", url):
            raise HTTPException(status_code=400,
                                detail="URL must start with https:// and end with .json")
        urls = board_urls()
        if url not in urls:
            urls.append(url)
            save_board_urls(urls)
        return {"success": True, "urls": urls}

    @app.delete("/v1/boards/urls")
    async def urls_remove(request: Request, url: str = "", c: Caller = Depends(admin)):
        url = url.strip() or await body_field(request, "url")
        urls = [u for u in board_urls() if u != url]
        save_board_urls(urls)
        return {"success": True, "urls": urls}

    # -- libraries (per user) ------------------------------------------------

    @app.get("/v1/libraries/installed")
    async def libs_installed(c: Caller = Depends(caller)):
        r = await arduino.run("lib", "list", "--format", "json", user_dir=user_dir(c))
        if not r.ok:
            raise fail(r, "Listing libraries")
        return clib.parse_installed_libraries(r.json())

    @app.get("/v1/libraries/search")
    async def libs_search(query: str = "", c: Caller = Depends(caller)):
        query = query.strip()[:100]
        if not query:
            return []
        r = await arduino.run("lib", "search", query, "--format", "json",
                              user_dir=user_dir(c), timeout=180)
        if not r.ok:
            raise fail(r, "Library search")
        return clib.parse_library_search(r.json())[:100]

    @app.get("/v1/libraries/versions")
    async def libs_versions(name: str, c: Caller = Depends(caller)):
        if not LIB_NAME_RE.match(name):
            raise HTTPException(status_code=400, detail="Invalid library name.")
        r = await arduino.run("lib", "search", name, "--format", "json",
                              user_dir=user_dir(c), timeout=180)
        if not r.ok:
            raise fail(r, "Library search")
        for lib in clib._as_list(r.json(), "libraries"):
            if isinstance(lib, dict) and lib.get("name") == name:
                return {"versions": clib.library_versions(lib)}
        return {"versions": []}

    @app.post("/v1/libraries/install")
    async def libs_install(req: LibraryRequest, c: Caller = Depends(caller)):
        if c.user == "anonymous":
            raise HTTPException(status_code=403, detail="An API key is needed to install libraries.")
        if not LIB_NAME_RE.match(req.name) or (req.version and not VERSION_RE.match(req.version)):
            raise HTTPException(status_code=400, detail="Invalid library name or version.")
        spec = f"{req.name}@{req.version}" if req.version else req.name
        r = await arduino.run("lib", "install", spec, user_dir=user_dir(c),
                              timeout=settings.install_timeout_s)
        return {"success": r.ok, "logs": r.logs}

    @app.delete("/v1/libraries/uninstall")
    async def libs_uninstall(request: Request, name: str = "",
                             c: Caller = Depends(caller)):
        req = LibraryRequest(name=name.strip() or await body_field(request, "name"))
        if c.user == "anonymous":
            raise HTTPException(status_code=403, detail="An API key is needed to remove libraries.")
        if not LIB_NAME_RE.match(req.name):
            raise HTTPException(status_code=400, detail="Invalid library name.")
        r = await arduino.run("lib", "uninstall", req.name, user_dir=user_dir(c),
                              timeout=settings.install_timeout_s)
        return {"success": r.ok, "logs": r.logs}


    # -- assistant (online model; the provider key stays on the server) ------

    app.state.assistant_provider = asist.create_provider(settings)
    app.state.assistant_keys = KeyStore(
        settings.data_dir / "assistant_keys.json", settings.assistant_key_days)
    # Swappable so a test (or another mail provider) can replace delivery.
    app.state.assistant_send_key = mail.send_key
    #: ip -> monotonic time of its last key request.
    key_requests: dict[str, float] = {}
    #: Answers served today, for the daily ceiling.
    daily = {"day": "", "count": 0}

    @app.get("/v1/assistant/info")
    async def assistant_info(c: Caller = Depends(caller)):
        provider = app.state.assistant_provider
        enabled = settings.assistant_enabled and provider is not None
        return {"enabled": enabled, "model": provider.model if enabled else None}

    @app.post("/v1/assistant/request-key")
    async def assistant_request_key(request: Request):
        """Mints an access key and emails it to the operator.

        Never returns the key: it goes to the operator's inbox, and they hand
        it out. The response is identical whether or not a mail was sent, so
        the endpoint cannot be probed.
        """
        provider = app.state.assistant_provider
        if not settings.assistant_enabled or provider is None:
            raise HTTPException(
                status_code=501,
                detail="The assistant is not set up on this server.",
            )
        ip = request.client.host if request.client else "unknown"
        limiter.check(f"keyreq:{ip}", "keyreq", 3)
        now = time.monotonic()
        if now - key_requests.get(ip, 0.0) < settings.assistant_key_cooldown_s:
            return {"requested": True}
        key_requests[ip] = now
        raw_key = app.state.assistant_keys.create(label=ip)
        try:
            channel = app.state.assistant_send_key(raw_key, requested_by=ip)
        except Exception as error:  # noqa: BLE001 - the user cannot fix mail
            print(f"[assistant] the key could not be sent: {error}", flush=True)
            channel = "failed"
        print(f"[assistant] key requested from {ip}, delivery: {channel}",
              flush=True)
        return {"requested": True}

    @app.post("/v1/assistant/chat")
    async def assistant_chat(req: asist.ChatRequest, request: Request,
                             c: Caller = Depends(caller)):
        provider = app.state.assistant_provider
        if not settings.assistant_enabled or provider is None:
            raise HTTPException(
                status_code=501,
                detail="The assistant is not set up on this server.",
            )

        bucket = c.key
        if c.user == "anonymous":
            # The public app carries no API key, so access comes from the word
            # emailed to the operator. It is only a pass for the assistant: it
            # can never identify anyone for compiling or installing.
            word = request.headers.get("X-Assistant-Word", "").strip()
            if word:
                if not (word in settings.assistant_words
                        or app.state.assistant_keys.accepts(word)):
                    raise HTTPException(
                        status_code=403,
                        detail="That assistant key is not valid. Request a new "
                               "one from the app.",
                    )
                bucket = "word:" + word.lower()
            elif not settings.assistant_allow_anonymous:
                raise HTTPException(
                    status_code=403,
                    detail="An assistant key is needed. Request one from the "
                           "app.",
                )

        # A rate limit caps speed, not the bill: this is the ceiling.
        today = time.strftime("%Y-%m-%d")
        if daily["day"] != today:
            daily["day"], daily["count"] = today, 0
        if (settings.assistant_daily_cap > 0
                and daily["count"] >= settings.assistant_daily_cap):
            raise HTTPException(
                status_code=429,
                detail="The assistant has reached its daily limit. Try again "
                       "tomorrow.",
            )

        limiter.check(bucket, "assistant", settings.assistant_per_minute)
        daily["count"] += 1
        messages = asist.messages_for(req)
        return StreamingResponse(
            asist.stream_answer(provider, messages, req),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # -- auto format (Arduino's ClangFormat style) ----------------------------

    format_config = Path(__file__).with_name("arduino.clang-format")

    async def _clang_format(code: str, style: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            settings.clang_format, f"--style={style}", "--assume-filename=sketch.cpp",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(code.encode()), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", "Timed out"
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    @app.post("/v1/format")
    async def format_code(req: FormatRequest, c: Caller = Depends(caller)):
        if len(req.code.encode()) > settings.max_sketch_bytes:
            raise HTTPException(status_code=413, detail="Code is too large.")
        try:
            rc, out, err = await _clang_format(req.code, f"file:{format_config}")
            if rc != 0:
                # Older clang-format versions may reject newer options.
                rc, out, err = await _clang_format(
                    req.code, "{BasedOnStyle: LLVM, IndentWidth: 2, ColumnLimit: 0, "
                    "AllowShortFunctionsOnASingleLine: Empty}")
        except FileNotFoundError:
            raise HTTPException(status_code=501, detail="clang-format is not installed on the server.")
        if rc != 0:
            raise HTTPException(status_code=502, detail=f"Formatting failed: {err[-500:]}")
        return {"formatted": out}

    # -- examples ------------------------------------------------------------

    EXAMPLE_FILE_RE = re.compile(r"\.(ino|h|hpp|c|cpp|S|txt)$")

    def _example_dirs(root: Path) -> list[Path]:
        """Folders that contain a .ino file named like the folder."""
        found = []
        for ino in sorted(root.rglob("*.ino")):
            if ino.stem == ino.parent.name:
                found.append(ino.parent)
        return found

    def _read_example(folder: Path, root: Path) -> dict:
        folder = folder.resolve()
        if root.resolve() not in folder.parents and folder != root.resolve():
            raise HTTPException(status_code=400, detail="Invalid example.")
        files, total = [], 0
        for p in sorted(folder.iterdir()):
            if p.is_file() and EXAMPLE_FILE_RE.search(p.name) and len(files) < settings.max_files:
                text = p.read_text(encoding="utf-8", errors="replace")
                total += len(text)
                if total > settings.max_sketch_bytes:
                    break
                files.append({"name": p.name, "content": text})
        main_name = f"{folder.name}.ino"
        files.sort(key=lambda f: (f["name"] != main_name, f["name"]))
        return {"name": folder.name, "files": files}

    async def _library_examples(c: Caller, fqbn: str | None) -> list[dict]:
        args = ["lib", "examples", "--format", "json"]
        if fqbn and FQBN_RE.match(fqbn):
            args += ["--fqbn", fqbn]
        r = await arduino.run(*args, user_dir=user_dir(c), timeout=120)
        if not r.ok:
            return []
        return clib.parse_library_examples(r.json())

    @app.get("/v1/examples")
    async def examples_list(fqbn: str | None = None, c: Caller = Depends(caller)):
        groups = []
        root = settings.builtin_examples_dir
        if root and root.is_dir():
            for top in sorted(p for p in root.iterdir() if p.is_dir()):
                items = [
                    {"id": f"builtin/{d.relative_to(root).as_posix()}",
                     "name": d.relative_to(top).as_posix()}
                    for d in _example_dirs(top)
                ]
                if items:
                    groups.append({"name": top.name, "source": "builtin", "examples": items})
        for lib in await _library_examples(c, fqbn):
            items = []
            for path in lib["examples"]:
                rel = path.split("/examples/", 1)[-1] if "/examples/" in path else Path(path).name
                items.append({"id": f"lib/{lib['name']}/{rel}", "name": rel})
            if items:
                groups.append({"name": lib["name"], "source": "library", "examples": items})
        return {"groups": groups}

    @app.get("/v1/examples/get")
    async def examples_get(id: str, fqbn: str | None = None, c: Caller = Depends(caller)):
        if ".." in id or "\\" in id or len(id) > 300:
            raise HTTPException(status_code=400, detail="Invalid example id.")
        kind, _, rest = id.partition("/")
        if kind == "builtin" and settings.builtin_examples_dir:
            root = settings.builtin_examples_dir
            return _read_example(root / rest, root)
        if kind == "lib":
            name, _, rel = rest.partition("/")
            for lib in await _library_examples(c, fqbn):
                if lib["name"] != name:
                    continue
                for path in lib["examples"]:
                    if path.endswith("/" + rel) or Path(path).name == rel:
                        p = Path(path)
                        root = (Path(path.split("/examples/", 1)[0]) / "examples"
                                if "/examples/" in path else p.parent)
                        return _read_example(p, root)
        raise HTTPException(status_code=404, detail="Example not found.")

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=getattr(exc, "headers", None))

    return app

