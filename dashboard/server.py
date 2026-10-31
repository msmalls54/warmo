"""Local, read-only WARMO camera and durable-memory dashboard."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
CACHE = {"state": {}, "latest_line": "Waiting for the saved mission…", "connected": False,
         "memory_updated_at": None, "error": "Connecting to Terse…",
         "camera": {"connected": False, "updated_at": None, "error": "Connecting to camera…"}}
JPEG = None
STOP = threading.Event()


def now():
    return datetime.now(timezone.utc).isoformat()


def camera_loop(args):
    global JPEG
    import cv2
    import numpy as np

    while not STOP.is_set():
        try:
            with urlopen(args.xiao_url + "/capture", timeout=3) as response:
                data = response.read(3_000_001)
            if len(data) > 3_000_000:
                raise ValueError("Camera frame too large")
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("Invalid camera photo")
            rotations = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
            if args.rotate in rotations:
                frame = cv2.rotate(frame, rotations[args.rotate])
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise ValueError("Photo encoding failed")
            with LOCK:
                JPEG = encoded.tobytes()
                CACHE["camera"] = {"connected": True, "updated_at": now(), "error": None}
        except Exception:
            with LOCK:
                CACHE["camera"]["connected"] = False
                CACHE["camera"]["error"] = "Camera unavailable; showing the last received photo."
        STOP.wait(2)


def memory_loop(args):
    helper = ROOT.parent / "terse_memory.py"
    while not STOP.is_set():
        try:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TERSE_TIMEOUT_S": "4"}
            result = subprocess.run([args.memory_python, str(helper), "state", args.mission, "{}"],
                                    capture_output=True, text=True, timeout=6, env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            value = json.loads(result.stdout)
            if result.returncode or value.get("ok") is not True or not isinstance(value.get("result"), dict):
                raise ValueError("Memory unavailable")
            state = value["result"]
            sightings = str(state.get("sightings", "")).split(" | ")
            latest = re.sub(r"^#\d+\s+@[-\d.]+deg:\s*", "", sightings[-1]).strip()
            if not latest:
                latest = "No observation has been saved for this mission yet."
            with LOCK:
                CACHE.update(state=state, latest_line=latest, connected=True,
                             memory_updated_at=now(), error=None)
        except Exception:
            with LOCK:
                CACHE.update(connected=False, error="Terse unavailable; last successfully read memory is retained.")
        STOP.wait(3)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host", "").split(":")[0] not in {"127.0.0.1", "localhost"}:
            return self.send(403, b"Local dashboard only", "text/plain")
        request = urlsplit(self.path)
        path = request.path
        if path in {"/", "/index.html"}:
            if ARGS.example and parse_qs(request.query).get("example") != ["1"]:
                self.send_response(302)
                self.send_header("Location", "/?example=1")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                return self.end_headers()
            return self.send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        examples = {"/example/state.json": ("state.json", "application/json; charset=utf-8"),
                    "/example/camera.jpg": ("camera.jpg", "image/jpeg")}
        if path in examples:
            filename, content_type = examples[path]
            return self.send(200, (ROOT / "example" / filename).read_bytes(), content_type)
        if path == "/api/state":
            if ARGS.example:
                return self.send(200, (ROOT / "example/state.json").read_bytes(), "application/json; charset=utf-8")
            with LOCK:
                body = json.dumps({**CACHE, "mission_id": ARGS.mission, "updated_at": now()}, ensure_ascii=False).encode()
            return self.send(200, body, "application/json; charset=utf-8")
        if path == "/api/camera.jpg":
            if ARGS.example:
                return self.send(200, (ROOT / "example/camera.jpg").read_bytes(), "image/jpeg")
            with LOCK:
                photo = JPEG
            return self.send(200, photo, "image/jpeg") if photo else self.send(503, b"Camera not ready", "text/plain")
        if path == "/health":
            return self.send(200, b'{"ok":true,"read_only":true}', "application/json")
        return self.send(404, b"Not found", "text/plain")


def main():
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--xiao-url", default=os.getenv("XIAO_URL", "http://192.168.4.1"))
    parser.add_argument("--mission", default=os.getenv("MISSION_ID", "warmo-3"))
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=270)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--frozen-image", type=Path, help="Display this saved JPEG without contacting the camera")
    source.add_argument("--example", action="store_true", help="Show the bundled recorded demo; no tank, Terse, or extra packages needed")
    parser.add_argument("--memory-python", default=os.getenv("WARMO_TERSE_PYTHON", str(Path.home() / "hw-test/warmo-terse-runtime/.venv/Scripts/python.exe")))
    ARGS = parser.parse_args()
    parsed = urlsplit(ARGS.xiao_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        parser.error("Use a plain http://TANK_IP camera address")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", ARGS.mission):
        parser.error("Invalid mission ID")
    ARGS.xiao_url = ARGS.xiao_url.rstrip("/")
    if ARGS.frozen_image:
        global JPEG
        JPEG = ARGS.frozen_image.read_bytes()
        if not JPEG.startswith(b"\xff\xd8") or not JPEG.endswith(b"\xff\xd9"):
            parser.error("The saved camera image must be a JPEG")
        meta = ARGS.frozen_image.with_suffix(".json")
        captured = json.loads(meta.read_text()).get("camera", {}).get("updated_at") if meta.exists() else None
        CACHE["camera"] = {"connected": False, "frozen": True, "updated_at": captured, "error": None}
    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    workers = () if ARGS.example else (memory_loop,) if ARGS.frozen_image else (camera_loop, memory_loop)
    for target in workers:
        threading.Thread(target=target, args=(ARGS,), daemon=True).start()
    query = "/?example=1" if ARGS.example else "/"
    print(f"WARMO camera page: http://127.0.0.1:{ARGS.port}{query} | read-only | {'recorded example' if ARGS.example else 'mission ' + ARGS.mission}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
