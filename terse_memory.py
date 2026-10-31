"""One-shot JSON bridge to the real Terse Mission actor (no local memory fallback).

Usage: python terse_memory.py start|state|record_patrol MISSION_ID JSON_PAYLOAD
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def invoke(action: str, mission_id: str, payload: dict[str, Any]) -> Any:
    import httpx
    from durable_actors import Client

    if action == "start":
        args = [str(payload.get("goal", "Patrol the demo area and report what you see."))]
    elif action == "state":
        args = []
    elif action == "record_patrol":
        args = [
            str(payload["action"]), str(payload.get("saw", "")),
            float(payload.get("turned_deg", 0)), float(payload.get("moved_cm", 0)),
        ]
    else:
        raise ValueError("Unsupported mission-memory action")
    origin = os.getenv("TERSE_BASE_URL") or os.getenv("DURABLE_ACTORS_CONTROL_PLANE_URL", "http://127.0.0.1:7100")
    # A short connection timeout fails clearly when the VM/runtime is stopped;
    # actor startup may take longer under the portable Linux emulator.
    timeout = httpx.Timeout(float(os.getenv("TERSE_TIMEOUT_S", "45")), connect=3.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as http:
        with Client(control_plane_url=origin, project_id=os.getenv("DURABLE_ACTORS_PROJECT_ID", "local"), http=http) as client:
            return client.invoke("Mission", mission_id, action, args)


def main() -> int:
    try:
        if len(sys.argv) != 4:
            raise ValueError("Expected action, mission ID, and JSON object payload")
        payload = json.loads(sys.argv[3])
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")
        result = invoke(sys.argv[1], sys.argv[2], payload)
        print(json.dumps({"ok": True, "result": result}, allow_nan=False), flush=True)
        return 0
    except Exception as error:
        # Do not print URLs/headers/credentials from an exception's arbitrary text.
        kind = type(error).__name__
        print(json.dumps({"ok": False, "error": f"Terse mission memory unavailable or request invalid ({kind}). Start the WARMO Terse VM/runtime and its local tunnel; no memory was fabricated."}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
