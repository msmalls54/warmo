"""Check a tank connection and optionally change only WARMO's Desktop mode."""
from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path.home() / "hw-test/warmo-desktop-runtime"
CONFIG = Path.home() / "AppData/Roaming/Claude/claude_desktop_config.json"


def check_real(url: str) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Use a plain http://TANK_IP URL")
    address = ipaddress.ip_address(parsed.hostname or "")
    if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("Use the replacement tank's confirmed private LAN IP address")
    origin = url.rstrip("/")
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        response = client.get(origin + "/status")
        response.raise_for_status()
        status = response.json()
        if not (status.get("name") == "WARMO" and status.get("protocol") == 1
                and status.get("camera_ok") is True and status.get("pwm") is True
                and status.get("motors_on") is False and not status.get("simulated")):
            raise RuntimeError("WARMO firmware, camera, PWM, or idle-state check failed")
        photo = client.get(origin + "/capture")
        photo.raise_for_status()
        if not photo.headers.get("content-type", "").startswith("image/jpeg"):
            raise RuntimeError("The camera returned no JPEG content")
        if not (photo.content.startswith(b"\xff\xd8") and photo.content.endswith(b"\xff\xd9")):
            raise RuntimeError("The camera returned an incomplete JPEG")
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(photo.content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("The camera JPEG could not be decoded")
        return {"xiao_url": origin, "status": status, "camera_shape": list(frame.shape),
                "camera_jpeg_bytes": len(photo.content), "movement_commands_sent": False}


def update_warmo(mode: str, origin: str | None, apply: bool) -> dict:
    spec = importlib.util.spec_from_file_location("warmo_config", ROOT / "connect_claude.py")
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    original = CONFIG.read_bytes()
    before = helper.read_json(original)
    old_entry = before["mcpServers"]["warmo"]
    if old_entry.get("args") != [str(ROOT / "manufact-v2/desktop.mjs")]:
        raise RuntimeError("The WARMO entry is not the verified Manufact v2 launcher")
    entry = json.loads(json.dumps(old_entry))
    entry.setdefault("env", {})["SIM"] = "1" if mode == "SIM" else "0"
    if origin:
        entry["env"]["XIAO_URL"] = origin
    text = original.decode("utf-8-sig")
    servers_start, servers_end = helper._root_value_span(text, "mcpServers")
    start, end = helper._root_value_span(text[servers_start:servers_end], "warmo")
    start += servers_start
    end += servers_start
    bom = codecs.BOM_UTF8 if original.startswith(codecs.BOM_UTF8) else b""
    encoded = json.dumps(entry, indent=2, ensure_ascii=False)
    updated = bom + (text[:start] + encoded + text[end:]).encode("utf-8")
    after = helper.read_json(updated)
    comparison = json.loads(json.dumps(after))
    comparison["mcpServers"]["warmo"] = old_entry
    if comparison != before:
        raise RuntimeError("Existing settings would change; refusing the update")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"mode": mode, "applied": False, "other_settings_preserved": True,
              "movement_commands_sent": False, "config_sha256_before": hashlib.sha256(original).hexdigest()}
    if apply and updated != original:
        # Padding leaves an existing file intact without deleting or replacing it.
        if len(updated) < len(original):
            updated += b" " * (len(original) - len(updated))
        backup = ROOT / f"claude-config-before-{mode.lower()}-{stamp}.json"
        with helper._exclusive_writer(CONFIG) as destination:
            if destination.read() != original:
                raise RuntimeError("Claude changed settings during preparation; inspect before retrying")
            backup.write_bytes(original)
            destination.seek(0)
            destination.write(updated)
            destination.flush()
            os.fsync(destination.fileno())
            destination.seek(0)
            if destination.read() != updated:
                raise RuntimeError("Configuration verification failed; original backup is preserved")
        report["applied"] = True
        report["backup"] = str(backup)
    elif apply:
        report["applied"] = True
    (ROOT / f"mode-check-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--sim", action="store_true")
    modes.add_argument("--real", action="store_true")
    parser.add_argument("--xiao-url")
    parser.add_argument("--apply", action="store_true", help="Update only the existing WARMO Desktop entry")
    args = parser.parse_args()
    mode = "REAL" if args.real else "SIM"
    if args.real and not args.xiao_url:
        parser.error("REAL requires --xiao-url from the replacement board's serial output")
    checked = check_real(args.xiao_url) if args.real else None
    report = update_warmo(mode, checked["xiao_url"] if checked else None, args.apply)
    print(json.dumps({"connection": checked, "desktop": report}, indent=2))
    if args.apply:
        print("Restart Claude Desktop to load this mode. No motors were commanded.")
    else:
        print("Read-only check: Desktop settings were not changed. Add --apply to change WARMO's mode.")


if __name__ == "__main__":
    main()
