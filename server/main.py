import os
import base64
import shutil
import tempfile
import subprocess
import re
import json
import hashlib
import multiprocessing
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Arduino Cloud Compiler")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

ARDUINO_CLI = "/usr/local/bin/arduino-cli"
PERSISTENT_BUILD_DIR = Path("/tmp/arduino-build-cache")
PERSISTENT_BUILD_DIR.mkdir(exist_ok=True)
JOBS = multiprocessing.cpu_count()

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

@app.get("/")
async def root():
    return {"status": "Arduino Cloud Compiler is running"}

@app.get("/health")
async def health():
    return {"status": "ok", "cli": shutil.which(ARDUINO_CLI) is not None}

@app.get("/v1/libraries")
async def list_libraries():
    rc, stdout, _ = run_cmd([ARDUINO_CLI, "lib", "list", "--format", "json"], 30)
    return json.loads(stdout) if rc == 0 else []

@app.post("/v1/compile", response_model=CompileResponse)
async def compile_sketch(req: CompileRequest):
    logs = []
    
    # Auto-detect + user libraries
    auto_libs = auto_detect_libraries(req.sketch)
    all_libs = list(dict.fromkeys(auto_libs + req.libraries))
    logs.append(f"Libraries needed: {all_libs}")
    
    # Install missing (cached check)
    install_libraries(all_libs, logs)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        sketch_dir = Path(tmpdir) / "sketch"
        sketch_dir.mkdir()
        (sketch_dir / "sketch.ino").write_text(req.sketch, encoding="utf-8")
        
        # Persistent build dir for caching
        sketch_hash = hashlib.md5((req.fqbn + req.sketch).encode()).hexdigest()[:16]
        build_dir = PERSISTENT_BUILD_DIR / f"{req.fqbn.replace(':', '_')}_{sketch_hash}"
        build_dir.mkdir(parents=True, exist_ok=True)
        
        cache_dir = PERSISTENT_BUILD_DIR / ".cache"
        cache_dir.mkdir(exist_ok=True)

        cmd = [
            ARDUINO_CLI,
            "compile",
            "--fqbn", req.fqbn,
            "--output-dir", str(build_dir),
            "--build-path", str(build_dir / ".build"),
            "--build-cache-path", str(cache_dir),
            "--jobs", str(JOBS),
            "--build-property", "compiler.optimization_flags=-Os",
            str(sketch_dir),
        ]

        rc, stdout, stderr = run_cmd(cmd, 300)
        logs.append(stdout)
        logs.append(stderr)

        if rc != 0:
            return CompileResponse(success=False, logs="\n".join(logs))

        hex_files = list(build_dir.glob("*.hex"))
        if not hex_files:
            return CompileResponse(success=False, logs="\n".join(logs) + "\n\nNo .hex generated")

        hex_data = base64.b64encode(hex_files[0].read_bytes()).decode()
        
        bin_files = list(build_dir.glob("*.bin"))
        bin_data = base64.b64encode(bin_files[0].read_bytes()).decode() if bin_files else None

        return CompileResponse(success=True, logs="\n".join(logs), hex=hex_data, binary=bin_data)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
