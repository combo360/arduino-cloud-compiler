#!/usr/bin/env python3
"""Stand-in for arduino-cli used by the tests.

Records every call (argv + ARDUINO_DIRECTORIES_USER) to $FAKE_CLI_LOG and
prints canned JSON in the arduino-cli 1.x format.
"""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"args": args, "user": os.environ.get("ARDUINO_DIRECTORIES_USER")}) + "\n")


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


cmd = args[:2]

#: header -> library that provides it
PROVIDES = {"Servo.h": "Servo", "Shared.h": "Shared", "MyMqtt.h": "MQTT"}

if cmd == ["compile", "--fqbn"]:
    fqbn = args[2]
    out_dir = Path(args[args.index("--output-dir") + 1])
    sketch_dir = Path(args[-1])
    ino = sketch_dir / f"{sketch_dir.name}.ino"
    source = ino.read_text()
    if "SYNTAX_ERROR" in source:
        print(f"{ino}:1:1: error: expected ';'", file=sys.stderr)
        sys.exit(1)
    # Headers provided by (fake) libraries: missing unless the library folder
    # exists in the user's folder or in a --libraries folder.
    lib_dirs = [Path(os.environ.get("ARDUINO_DIRECTORIES_USER", "/nonexistent")) / "libraries"]
    if "--libraries" in args:
        lib_dirs.append(Path(args[args.index("--libraries") + 1]))
    for line_no, line in enumerate(source.splitlines(), 1):
        if line.startswith("#include <") and line.endswith(".h>"):
            header = line[len("#include <"):-1]
            if header in ("Arduino.h",):
                continue
            lib = PROVIDES.get(header, "")
            if not any((d / lib).is_dir() for d in lib_dirs if lib):
                print(f"{ino}:{line_no}:10: fatal error: {header}: No such file or directory",
                      file=sys.stderr)
                sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sketch_dir.name
    extras = sorted(p.name for p in sketch_dir.iterdir() if p.name != ino.name)
    if fqbn.startswith("esp32:"):
        (out_dir / f"{stem}.ino.bin").write_bytes(b"\xe9BIN")
        (out_dir / f"{stem}.ino.bootloader.bin").write_bytes(b"BOOT")
        if "--build-path" in args:
            # What the arduino-esp32 3.x core writes into the build folder.
            bp = Path(args[args.index("--build-path") + 1])
            bp.mkdir(parents=True, exist_ok=True)
            (bp / f"{stem}.ino.bin").write_bytes(b"\xe9BIN")
            (bp / f"{stem}.ino.bootloader.bin").write_bytes(b"BOOT")
            (bp / f"{stem}.ino.partitions.bin").write_bytes(b"PART")
            (bp / "boot_app0.bin").write_bytes(b"OTA0")
            (bp / "flash_args").write_text(
                "--flash-mode dio --flash-freq 80m --flash-size 4MB\n"
                f"0x1000 {stem}.ino.bootloader.bin\n"
                f"0x8000 {stem}.ino.partitions.bin\n"
                "0xe000 boot_app0.bin\n"
                f"0x10000 {stem}.ino.bin\n")
    else:
        # newline="\n": without it Windows stores \r\n and the tests, which
        # read the hex back, only pass on Linux.
        (out_dir / f"{stem}.ino.hex").write_text(":00000001FF\n", newline="\n")
        (out_dir / f"{stem}.ino.with_bootloader.hex").write_text(":0000000000\n")
    print(f"Sketch uses 924 bytes. Extra files: {','.join(extras) or 'none'}")
    sys.exit(0)

if cmd == ["board", "listall"]:
    out({"boards": [{
        "name": "Arduino Uno",
        "fqbn": "arduino:avr:uno",
        "platform": {"metadata": {"id": "arduino:avr"}, "release": {"version": "1.8.6"}},
    }]})

if cmd[:2] == ["core", "search"]:
    out({"platforms": [{
        "metadata": {"id": "esp32:esp32"},
        "installed_version": "",
        "latest_version": "3.0.0",
        "releases": {"3.0.0": {"name": "esp32", "boards": [
            {"name": "ESP32 Dev Module"}, {"name": "XIAO_ESP32C3"}]}},
    }]})

if cmd[:2] in (["core", "install"], ["core", "uninstall"], ["core", "update-index"]):
    print("ok")
    sys.exit(0)

if cmd == ["lib", "list"]:
    out({"installed_libraries": [{"library": {
        "name": "Servo", "author": "Arduino", "version": "1.2.1",
        "sentence": "Controls servo motors.", "category": "Device Control",
        "architectures": ["*"],
    }}]})

if cmd == ["lib", "search"] and args[2].startswith("provides:"):
    header = args[2][len("provides:"):]
    names = [lib for h, lib in PROVIDES.items() if h == header]
    out({"libraries": [{"name": n} for n in names]})

if cmd == ["lib", "search"]:
    out({"libraries": [{
        "name": "MQTT",
        "latest": {"author": "Joel Gaehwiler", "version": "2.5.3",
                   "sentence": "MQTT library for Arduino", "category": "Communication",
                   "architectures": ["*"], "website": "https://github.com/256dpi/arduino-mqtt"},
        "available_versions": ["2.5.0", "2.5.3", "2.4.8"],
    }]})

if cmd == ["lib", "examples"]:
    user = os.environ.get("ARDUINO_DIRECTORIES_USER", "/tmp")
    out({"examples": [{
        "library": {"name": "Servo"},
        "examples": [f"{user}/libraries/Servo/examples/Sweep",
                     f"{user}/libraries/Servo/examples/Knob"],
    }]})

if cmd == ["lib", "install"]:
    if args[2] == "Broken":
        print("install failed", file=sys.stderr)
        sys.exit(1)
    user = Path(os.environ["ARDUINO_DIRECTORIES_USER"])
    (user / "libraries" / args[2].split("@")[0]).mkdir(parents=True, exist_ok=True)
    print(f"{args[2]} installed")
    sys.exit(0)

if cmd == ["lib", "uninstall"]:
    print("ok")
    sys.exit(0)

print(f"fake arduino-cli: unknown command {args}", file=sys.stderr)
sys.exit(2)
