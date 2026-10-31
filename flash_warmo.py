"""Validate the prepared firmware; flash only with explicit battery-off confirmation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path.home() / "hw-test"
PLAN = ROOT / "build/WarmoReady/upload-plan.json"


def verified_plan() -> dict:
    plan = json.loads(PLAN.read_text(encoding="utf-8-sig"))
    if plan.get("compile_pass") is not True or plan.get("psram_mode") != "opi":
        raise RuntimeError("The prepared OPI-PSRAM firmware has not passed compilation")
    if plan.get("chip") != "esp32s3":
        raise RuntimeError("This upload plan is not for the requested ESP32-S3")
    if not plan.get("static_checks", {}).get("passed"):
        raise RuntimeError("Firmware pin and motion checks have not passed")
    if not plan.get("credentials_configured"):
        raise RuntimeError("The prepared firmware still needs its private Wi-Fi configuration")
    if hashlib.sha256(Path(plan["source_path"]).read_bytes()).hexdigest() != plan["source_sha256"]:
        raise RuntimeError("Firmware source changed after compilation; rebuild before uploading")
    if len(plan["segments"]) != 4 or {int(part["addresshex"], 16) for part in plan["segments"]} != {0, 0x8000, 0xE000, 0x10000}:
        raise RuntimeError("Unexpected flash segments; refusing a merged or full-flash image")
    ranges = []
    for part in plan["segments"]:
        path = Path(part["pathabs"]).resolve(strict=True)
        if not path.is_relative_to(ROOT.resolve()):
            raise RuntimeError("A firmware segment is outside the test folder")
        if hashlib.sha256(path.read_bytes()).hexdigest().lower() != part["sha256"].lower():
            raise RuntimeError(f"Firmware checksum changed: {path.name}")
        ranges.append((int(part["addresshex"], 16), path.stat().st_size))
    ranges.sort()
    for index, (start, length) in enumerate(ranges):
        limit = ranges[index + 1][0] if index + 1 < len(ranges) else 8 * 1024 * 1024
        if length <= 0 or start + length > limit:
            raise RuntimeError("Flash segments overlap or exceed the XIAO's 8MB flash")
    if not Path(plan["esptoolpath"]).is_file():
        raise RuntimeError("The isolated ESP32 flashing tool was not found")
    return plan


def boot_ip_from_buffer(buffer: str) -> str | None:
    # readline() may time out in the middle of an address. Require a complete
    # firmware line so 192.168.137.74 cannot be mistaken for 192.168.137.7.
    pattern = r"(?m)^WARMO IP:[ \t]*(http://((?:\d{1,3}\.){3}\d{1,3}))[ \t]*\r?\n"
    for match in re.finditer(pattern, buffer):
        try:
            address = ipaddress.IPv4Address(match.group(2))
        except ipaddress.AddressValueError:
            continue
        if address.is_private and not (address.is_unspecified or address.is_loopback or address.is_multicast):
            return match.group(1)
    return None


def read_boot_ip(port: str, seconds: int) -> str | None:
    import serial

    deadline = time.monotonic() + seconds
    buffer = ""
    while time.monotonic() < deadline:
        try:
            connection = serial.Serial(port=None, baudrate=115200, timeout=0.5)
            connection.dtr = True
            connection.rts = False
            connection.port = port
            with connection:
                while time.monotonic() < deadline:
                    line = connection.readline().decode("utf-8", errors="replace")
                    if not line:
                        continue
                    print(line.rstrip(), flush=True)
                    buffer = (buffer + line)[-8192:]
                    address = boot_ip_from_buffer(buffer)
                    if address:
                        return address
        except (serial.SerialException, OSError):
            time.sleep(1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flash", action="store_true", help="Actually contact and flash the board")
    parser.add_argument("--battery-disconnected", action="store_true")
    parser.add_argument("--port", help="Confirmed USB serial port, for example COM6")
    parser.add_argument("--monitor-seconds", type=int, default=45)
    args = parser.parse_args()
    plan = verified_plan()
    print(f"Firmware verified: {plan['fqbn']}; four standard segments; OPI PSRAM.", flush=True)
    if not args.flash:
        print("Check only: no board was contacted. Flash requires --flash --port COMn --battery-disconnected.")
        return 0
    if not args.battery_disconnected:
        parser.error("Disconnect the separate motor battery first, then confirm with --battery-disconnected")
    if not args.port or not re.fullmatch(r"COM[1-9][0-9]*", args.port, re.IGNORECASE):
        parser.error("Supply the replacement XIAO's confirmed Windows COM port")
    print("ABOUT TO FLASH: USB board only, motor battery disconnected. No movement commands are sent.", flush=True)
    command = [plan["esptoolpath"], "--chip", "esp32s3", "--port", args.port,
               "--baud", "460800", "--before", "default-reset", "--after", "hard-reset", "write-flash"]
    for field, flag in [("flash_mode", "--flash-mode"), ("flash_freq", "--flash-freq"), ("flash_size", "--flash-size")]:
        command += [flag, plan[field]]
    for part in plan["segments"]:
        command += [part["addresshex"], part["pathabs"]]
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with (log_dir / f"warmo-upload-{stamp}.txt").open("x", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")
        assert process.stdout is not None
        for line in process.stdout:
            print(line.rstrip(), flush=True)
            log.write(line)
        if process.wait() != 0:
            raise RuntimeError("Upload failed; inspect the saved upload log. Close any open serial monitor.")
    print("Upload verified by the flashing tool. Reading boot output at 115200; no movement commands.", flush=True)
    address = read_boot_ip(args.port, max(5, min(120, args.monitor_seconds)))
    if not address:
        print("No WARMO IP received. Keep motor battery disconnected; check the test hotspot and USB port.")
        return 2
    record = {"xiao_url": address, "port": args.port, "recorded_utc": datetime.now(timezone.utc).isoformat(),
              "source": "WARMO serial boot output after verified upload", "hardware_demo_passed": False}
    destination = ROOT / "warmo-desktop-runtime" / f"connection-{stamp}.json"
    destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Connected at {address}. Saved {destination}. Motors have not been tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
