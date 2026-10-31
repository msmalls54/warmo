"""Passively detect an ESP32/XIAO USB port; never open it, flash it, or move motors."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from serial.tools import list_ports

STATE = Path.home() / "hw-test/warmo-desktop-runtime/usb-watch-state.json"


def candidates():
    return [
        {"port": item.device, "description": item.description, "vid": item.vid, "pid": item.pid}
        for item in list_ports.comports()
        if item.vid in (0x303A, 0x2886) or any(word in (item.description or "").lower() for word in ("esp32", "xiao"))
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, min(43200, args.timeout))
    previous = None
    if args.watch:
        print("Watching for the replacement XIAO. No serial port will be opened and no hardware command will be sent.", flush=True)
    while True:
        found = candidates()
        state = {"observed_utc": datetime.now(timezone.utc).isoformat(), "candidates": found,
                 "serial_ports_opened": False, "hardware_commands_sent": False,
                 "next_step": "Owner must confirm replacement board and motor battery disconnected before flashing"}
        STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        if found != previous:
            print(json.dumps(state), flush=True)
        if found or not args.watch:
            return 0
        previous = found
        if time.monotonic() >= deadline:
            print("USB watch finished: no replacement board detected. Nothing was flashed or moved.", flush=True)
            return 2
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
