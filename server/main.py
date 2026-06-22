import os
import base64
import shutil
import tempfile
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Arduino Cloud Compiler")

@app.get("/")
async def root():
    return {"status": "Arduino Cloud Compiler is running"}

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

class CompileResponse(BaseModel):
    success: bool
    logs: str
    hex: str | None = None
    binary: str | None = None

@app.post("/v1/compile", response_model=CompileResponse)
async def compile_sketch(req: CompileRequest):
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
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Compilation timed out")

        logs = (proc.stdout or "") + (proc.stderr or "")

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

@app.get("/health")
async def health():
    return {"status": "ok", "cli": shutil.which(ARDUINO_CLI) is not None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)