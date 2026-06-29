import os
import base64
import shutil
import tempfile
import subprocess
import re
import json
import hashlib
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Arduino Cloud Compiler")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "DELETE"],
    allow_headers=["*"],
)

ARDUINO_CLI = "/usr/local/bin/arduino-cli"
PERSISTENT_BUILD_DIR = Path("/tmp/arduino-build-cache")
PERSISTENT_BUILD_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CompileRequest(BaseModel):
    sketch: str
    fqbn: str = "arduino:avr:uno"
    optimize: str = "size"
    libraries: list[str] = Field(default_factory=list)

class CompileResponse(BaseModel):
    success: bool
    logs: str
    hex: str | None = None
    binary: str | None = None

class InstallRequest(BaseModel):
    name: str
    version: str | None = None

class BoardInstallRequest(BaseModel):
    core: str  # e.g. "arduino:avr", "esp32:esp32"

class AdditionalUrlRequest(BaseModel):
    url: str

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"

def get_installed_libraries() -> set[str]:
    rc, stdout, _ = run_cmd([ARDUINO_CLI, "lib", "list", "--format", "json"], 30)
    if rc != 0:
        return set()
    try:
        return {lib["library"]["name"].lower() for lib in json.loads(stdout)}
    except Exception:
        return set()

_INSTALLED_LIBS_CACHE: set[str] | None = None

def get_installed_libraries_cached() -> set[str]:
    global _INSTALLED_LIBS_CACHE
    if _INSTALLED_LIBS_CACHE is None:
        _INSTALLED_LIBS_CACHE = get_installed_libraries()
    return _INSTALLED_LIBS_CACHE

def extract_includes(sketch_code: str) -> set[str]:
    return set(re.findall(r'#include\s*[<"]([^>"]+)[>"]', sketch_code))

def resolve_library_name(include_name: str) -> str | None:
    base = include_name.replace(".h", "")
    lookup = base.lower()

    core_headers = {
        "arduino", "spi", "wire", "eeprom", "softwareserial", "hardwareserial",
        "wprogram", "wconstants", "pins_arduino", "avr/io", "avr/interrupt",
        "avr/pgmspace", "avr/sleep", "avr/wdt", "util/delay", "stdlib",
        "string", "math", "stdio", "stdint", "stdbool", "inttypes", "ctype",
        "time", "assert", "errno", "stddef", "limits", "float", "setjmp",
        "signal", "avr/power", "avr/eeprom", "avr/sfr_defs", "avr/common",
        "avr/version", "avr/fuse", "avr/lock", "avr/boot", "avr/cpufunc",
        "avr/builtins", "avr/io",
    }
    if lookup in core_headers:
        return None

    mappings = {
        "servo": "Servo",
        "stepper": "Stepper",
        "ethernet": "Ethernet",
        "sd": "SD",
        "wifinina": "WiFiNINA",
        "liquidcrystal": "LiquidCrystal",
        "liquidcrystal_i2c": "LiquidCrystal I2C",
        "dht": "DHT sensor library",
        "dht11": "DHT sensor library",
        "dht22": "DHT sensor library",
        "onewire": "OneWire",
        "dallastemperature": "DallasTemperature",
        "dallas_temperature": "DallasTemperature",
        "neopixel": "Adafruit NeoPixel",
        "adafruit_gfx": "Adafruit GFX Library",
        "adafruit_ssd1306": "Adafruit SSD1306",
        "adafruit_sensor": "Adafruit Unified Sensor",
        "adafruit_bmp280": "Adafruit BMP280 Library",
        "adafruit_bme280": "Adafruit BME280 Library",
        "adafruit_bme680": "Adafruit BME680 Library",
        "adafruit_mpu6050": "Adafruit MPU6050",
        "adafruit_neopixel": "Adafruit NeoPixel",
        "irremote": "IRremote",
        "fastled": "FastLED",
        "tmrpcm": "TMRpcm",
        "newping": "NewPing",
        "rf24": "RF24",
        "mpu6050": "MPU6050",
        "arduinojson": "ArduinoJson",
        "json": "ArduinoJson",
        "pubsubclient": "PubSubClient",
        "mqtt": "PubSubClient",
        "blynk": "Blynk",
        "thingspeak": "ThingSpeak",
        "wifimanager": "WiFiManager",
        "espasyncwebserver": "ESPAsyncWebServer",
        "asyncmqttclient": "AsyncMqttClient",
        "painlessmesh": "painlessMesh",
        "nimble": "NimBLE-Arduino",
        "tft_espi": "TFT_eSPI",
        "u8g2": "U8g2",
        "lvgl": "lvgl",
        "ledcontrol": "LedControl",
        "tm1637": "TM1637Display",
        "pcf8574": "PCF8574",
        "pcf8575": "PCF8575",
        "mcp23017": "Adafruit MCP23017 Arduino Library",
        "mcp3008": "Adafruit_MCP3008",
        "mcp4725": "Adafruit MCP4725",
        "mcp9808": "Adafruit MCP9808 Library",
    }
    return mappings.get(lookup, base)

def install_libraries(libraries: list[str], logs: list[str]) -> None:
    installed = get_installed_libraries_cached()
    to_install = [lib for lib in libraries if lib.lower() not in installed]
    if not to_install:
        logs.append("All libraries already installed.")
        return

    logs.append(f"Installing: {to_install}")
    for lib in to_install:
        rc, out, err = run_cmd([ARDUINO_CLI, "lib", "install", lib], 120)
        logs.append(out)
        if err:
            logs.append(f"WARN: {err}")
        if rc == 0:
            installed.add(lib.lower())

def auto_detect_libraries(sketch_code: str) -> list[str]:
    includes = extract_includes(sketch_code)
    libs = []
    for inc in includes:
        name = resolve_library_name(inc)
        if name:
            libs.append(name)
    return list(dict.fromkeys(libs))

# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH / ROOT
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"status": "Arduino Cloud Compiler is running"}

@app.get("/health")
async def health():
    return {"status": "ok", "cli": shutil.which(ARDUINO_CLI) is not None}

# ═══════════════════════════════════════════════════════════════════════════════
# COMPILATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/v1/libraries")
async def list_libraries():
    rc, stdout, _ = run_cmd([ARDUINO_CLI, "lib", "list", "--format", "json"], 30)
    return json.loads(stdout) if rc == 0 else []

@app.post("/v1/compile", response_model=CompileResponse)
async def compile_sketch(req: CompileRequest):
    logs = []

    auto_libs = auto_detect_libraries(req.sketch)
    all_libs = list(dict.fromkeys(auto_libs + req.libraries))
    logs.append(f"Libraries needed: {all_libs}")

    install_libraries(all_libs, logs)

    with tempfile.TemporaryDirectory() as tmpdir:
        sketch_dir = Path(tmpdir) / "sketch"
        sketch_dir.mkdir()
        (sketch_dir / "sketch.ino").write_text(req.sketch, encoding="utf-8")

        build_dir = PERSISTENT_BUILD_DIR / req.fqbn.replace(":", "_")
        build_dir.mkdir(parents=True, exist_ok=True)

        sketch_hash = hashlib.md5(req.sketch.encode()).hexdigest()[:12]
        output_dir = build_dir / f"out_{sketch_hash}"
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            ARDUINO_CLI,
            "compile",
            "--fqbn", req.fqbn,
            "--output-dir", str(output_dir),
            "--build-path", str(build_dir / ".build"),
            "--build-cache-path", str(build_dir / ".cache"),
            "--build-property", "compiler.optimization_flags=-Os",
            str(sketch_dir),
        ]

        rc, stdout, stderr = run_cmd(cmd, 300)
        logs.append(stdout)
        logs.append(stderr)

        if rc != 0:
            return CompileResponse(success=False, logs="\n".join(logs))

        hex_files = list(output_dir.glob("*.hex"))
        if not hex_files:
            return CompileResponse(success=False, logs="\n".join(logs) + "\n\nNo .hex generated")

        hex_data = base64.b64encode(hex_files[0].read_bytes()).decode()

        bin_files = list(output_dir.glob("*.bin"))
        bin_data = base64.b64encode(bin_files[0].read_bytes()).decode() if bin_files else None

        return CompileResponse(success=True, logs="\n".join(logs), hex=hex_data, binary=bin_data)

# ═══════════════════════════════════════════════════════════════════════════════
# BOARDS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/v1/boards/installed")
async def list_installed_boards():
    """List all installed board cores."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "core", "list", "--format", "json"], 30)
    if rc != 0:
        return []
    try:
        cores = json.loads(stdout)
        boards = []
        for core in cores:
            for board in core.get("boards", []):
                boards.append({
                    "name": board.get("name", "Unknown"),
                    "fqbn": board.get("fqbn", ""),
                    "core": core.get("id", ""),
                    "version": core.get("installed", ""),
                })
        return boards
    except Exception:
        return []

@app.get("/v1/boards/search")
async def search_boards(query: str = Query("")):
    """Search available board cores."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "core", "search", query, "--format", "json"], 60)
    if rc != 0:
        return []
    try:
        return json.loads(stdout)
    except Exception:
        return []

@app.post("/v1/boards/install")
async def install_board(req: BoardInstallRequest):
    """Install a board core."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "core", "install", req.core], 300)
    success = rc == 0
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

@app.delete("/v1/boards/uninstall")
async def uninstall_board(core: str = Query(...)):
    """Uninstall a board core."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "core", "uninstall", core], 120)
    success = rc == 0
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

@app.get("/v1/boards/urls")
async def get_additional_urls():
    """Get additional boards manager URLs."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "config", "dump", "--format", "json"], 30)
    if rc != 0:
        return {"urls": []}
    try:
        config = json.loads(stdout)
        urls = config.get("board_manager", {}).get("additional_urls", [])
        return {"urls": urls if isinstance(urls, list) else [urls]}
    except Exception:
        return {"urls": []}

@app.post("/v1/boards/urls")
async def add_additional_url(req: AdditionalUrlRequest):
    """Add an additional boards manager URL."""
    rc, stdout, stderr = run_cmd(
        [ARDUINO_CLI, "config", "add", "board_manager.additional_urls", req.url], 30
    )
    # Update index after adding URL
    run_cmd([ARDUINO_CLI, "core", "update-index"], 60)
    success = rc == 0
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

@app.delete("/v1/boards/urls")
async def remove_additional_url(url: str = Query(...)):
    """Remove an additional boards manager URL."""
    rc, stdout, stderr = run_cmd(
        [ARDUINO_CLI, "config", "remove", "board_manager.additional_urls", url], 30
    )
    success = rc == 0
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# LIBRARY MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/v1/libraries/installed")
async def list_installed_libraries_detailed():
    """List all installed libraries with details."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "lib", "list", "--format", "json"], 30)
    if rc != 0:
        return []
    try:
        data = json.loads(stdout)
        libraries = []
        for item in data:
            lib = item.get("library", {})
            libraries.append({
                "name": lib.get("name", ""),
                "author": lib.get("author", ""),
                "version": lib.get("version", ""),
                "sentence": lib.get("sentence", ""),
                "paragraph": lib.get("paragraph", ""),
                "url": lib.get("website", ""),
                "category": lib.get("category", ""),
                "architectures": lib.get("architectures", []),
                "install_dir": lib.get("install_dir", ""),
            })
        return libraries
    except Exception:
        return []

@app.get("/v1/libraries/search")
async def search_libraries(query: str = Query("")):
    """Search available libraries."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "lib", "search", query, "--format", "json"], 60)
    if rc != 0:
        return []
    try:
        data = json.loads(stdout)
        return data.get("libraries", [])
    except Exception:
        return []

@app.post("/v1/libraries/install")
async def install_library(req: InstallRequest):
    """Install a library."""
    cmd = [ARDUINO_CLI, "lib", "install", req.name]
    if req.version:
        cmd.extend(["--version", req.version])
    rc, stdout, stderr = run_cmd(cmd, 300)
    success = rc == 0
    # Invalidate cache
    global _INSTALLED_LIBS_CACHE
    _INSTALLED_LIBS_CACHE = None
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

@app.delete("/v1/libraries/uninstall")
async def uninstall_library(name: str = Query(...)):
    """Uninstall a library."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "lib", "uninstall", name], 120)
    success = rc == 0
    global _INSTALLED_LIBS_CACHE
    _INSTALLED_LIBS_CACHE = None
    return {
        "success": success,
        "message": stdout if success else stderr,
    }

@app.get("/v1/libraries/versions")
async def list_library_versions(name: str = Query(...)):
    """List available versions of a library."""
    rc, stdout, stderr = run_cmd([ARDUINO_CLI, "lib", "search", name, "--format", "json"], 60)
    if rc != 0:
        return []
    try:
        data = json.loads(stdout)
        for lib in data.get("libraries", []):
            if lib.get("name", "").lower() == name.lower():
                return lib.get("available_versions", [])
        return []
    except Exception:
        return []

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
