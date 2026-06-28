import os
import base64
import shutil
import tempfile
import subprocess
import re
import json
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

class CompileRequest(BaseModel):
    sketch: str
    fqbn: str = "arduino:avr:uno"
    optimize: str = "size"
    libraries: list[str] = Field(default_factory=list, description="Extra libraries to install")

class CompileResponse(BaseModel):
    success: bool
    logs: str
    hex: str | None = None
    binary: str | None = None

def get_installed_libraries() -> set[str]:
    """Get set of already installed library names (case-insensitive)."""
    try:
        result = subprocess.run(
            [ARDUINO_CLI, "lib", "list", "--format", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return set()
        libs = json.loads(result.stdout)
        return {lib["library"]["name"].lower() for lib in libs}
    except Exception:
        return set()

def extract_includes(sketch_code: str) -> set[str]:
    """Extract #include <Library.h> or #include "Library.h" from sketch."""
    pattern = r'#include\s*[<"]([^>"]+)[>"]'
    return set(re.findall(pattern, sketch_code))

def resolve_library_name(include_name: str) -> str | None:
    """
    Map header file to library name.
    Many match directly, but some don't (e.g., WiFi.h -> WiFiNINA or WiFi)
    """
    # Strip .h extension for direct mapping
    base = include_name.replace(".h", "")
    
    # Known mappings where header != library name
    mappings = {
        "wifi": "WiFiNINA",           # or "WiFi" for ESP32
        "wifiserver": "WiFiNINA",
        "wificlient": "WiFiNINA",
        "softwareserial": "SoftwareSerial",
        "eeprom": "EEPROM",
        "spi": "SPI",
        "wire": "Wire",
        "servo": "Servo",
        "stepper": "Stepper",
        "ethernet": "Ethernet",
        "sd": "SD",
        "liquidcrystal": "LiquidCrystal",
        "neopixel": "Adafruit NeoPixel",
        "dht": "DHT sensor library",
        "onewire": "OneWire",
        "liquidcrystal_i2c": "LiquidCrystal I2C",
        "tmrpcm": "TMRpcm",
        "rf24": "RF24",
        "mpu6050": "MPU6050",
        "servotimer2": "ServoTimer2",
        "altsoftserial": "AltSoftSerial",
        "newping": "NewPing",
        "irremote": "IRremote",
    }
    
    lookup = base.lower()
    if lookup in mappings:
        return mappings[lookup]
    
    # Try direct match first
    return base

def install_libraries(libraries: list[str], logs: list[str]) -> bool:
    """Install missing libraries. Returns True if all succeeded."""
    installed = get_installed_libraries()
    to_install = []
    
    for lib in libraries:
        if lib.lower() not in installed:
            to_install.append(lib)
    
    if not to_install:
        return True
    
    logs.append(f"Installing libraries: {', '.join(to_install)}")
    
    for lib in to_install:
        try:
            result = subprocess.run(
                [ARDUINO_CLI, "lib", "install", lib],
                capture_output=True, text=True, timeout=120
            )
            logs.append(result.stdout or "")
            if result.stderr:
                logs.append(result.stderr)
            if result.returncode != 0:
                logs.append(f"WARNING: Failed to install {lib}")
                # Don't fail immediately — let compilation try anyway
        except subprocess.TimeoutExpired:
            logs.append(f"TIMEOUT installing {lib}")
    
    return True

def auto_detect_libraries(sketch_code: str) -> list[str]:
    """Auto-detect required libraries from #include directives."""
    includes = extract_includes(sketch_code)
    libraries = []
    
    # Built-in/core headers to skip (part of the core, not libraries)
    core_headers = {
        "arduino.h", "avr/io.h", "avr/interrupt.h", "avr/pgmspace.h",
        "avr/sleep.h", "avr/wdt.h", "util/delay.h", "stdlib.h", 
        "string.h", "math.h", "stdio.h", "stdint.h", "stdbool.h",
        "inttypes.h", "ctype.h", "time.h", "assert.h", "errno.h",
        "stddef.h", "limits.h", "float.h", "setjmp.h", "signal.h",
    }
    
    for include in includes:
        if include.lower() in core_headers:
            continue
        
        lib_name = resolve_library_name(include)
        if lib_name:
            libraries.append(lib_name)
    
    return libraries

@app.get("/")
async def root():
    return {"status": "Arduino Cloud Compiler is running"}

@app.get("/health")
async def health():
    cli_ok = shutil.which(ARDUINO_CLI) is not None
    return {"status": "ok", "cli": cli_ok}

@app.get("/v1/libraries")
async def list_libraries():
    """List all installed libraries."""
    try:
        result = subprocess.run(
            [ARDUINO_CLI, "lib", "list", "--format", "json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr)
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"libraries": []}

@app.post("/v1/compile", response_model=CompileResponse)
async def compile_sketch(req: CompileRequest):
    log_lines = []
    
    # Auto-detect + user-specified libraries
    auto_libs = auto_detect_libraries(req.sketch)
    all_libs = list(dict.fromkeys(auto_libs + req.libraries))  # preserve order, remove dups
    log_lines.append(f"Detected libraries: {all_libs}")
    
    # Install missing libraries
    install_libraries(all_libs, log_lines)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        sketch_dir = Path(tmpdir) / "sketch"
        sketch_dir.mkdir()
        sketch_file = sketch_dir / "sketch.ino"
        sketch_file.write_text(req.sketch, encoding="utf-8")
        
        build_dir = Path(tmpdir) / "build"
        build_dir.mkdir()

        cmd = [
            ARDUINO_CLI,
            "compile",
            "--fqbn", req.fqbn,
            "--output-dir", str(build_dir),
            "--build-property", "compiler.optimization_flags=-Os",
            "--verbose",
            str(sketch_dir),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # Increased for library installs
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Compilation timed out")

        logs = "\n".join(log_lines) + "\n\n" + (proc.stdout or "") + (proc.stderr or "")

        if proc.returncode != 0:
            return CompileResponse(success=False, logs=logs)

        hex_files = list(build_dir.glob("*.hex"))
        if not hex_files:
            return CompileResponse(
                success=False,
                logs=logs + "\n\nError: No .hex file generated",
            )

        hex_path = hex_files[0]
        hex_data = base64.b64encode(hex_path.read_bytes()).decode("utf-8")

        bin_files = list(build_dir.glob("*.bin"))
        bin_data = None
        if bin_files:
            bin_data = base64.b64encode(bin_files[0].read_bytes()).decode("utf-8")

        return CompileResponse(
            success=True,
            logs=logs,
            hex=hex_data,
            binary=bin_data,
        )

@app.post("/v1/install-libs")
async def install_libraries_endpoint(libraries: list[str]):
    """Manually install libraries."""
    logs = []
    install_libraries(libraries, logs)
    return {"installed": libraries, "logs": "\n".join(logs)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
