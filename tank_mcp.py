"""WARMO patrol/scout MCP server (Manufact / mcp-use).

Exposes camera observations, patrol status, bounded movement, and stopping.

SIM=1  -> patrol simulation (no hardware needed)
SIM=0  -> real XIAO at XIAO_URL (e.g. http://172.20.10.5)

Run:  uv run tank_mcp.py        (serves http://localhost:8000/mcp, inspector at http://localhost:8000/)
"""
from __future__ import annotations

import math
import os
import random
import sys
import threading
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("MCP_USE_ANONYMIZED_TELEMETRY", "false")

from mcp_use.server import MCPServer  # noqa: E402
from mcp.types import ImageContent, TextContent  # noqa: E402

# ---------------------------------------------------------------- config ----
SIM = os.getenv("SIM", "1") == "1"
DESKTOP_STDIO = os.getenv("MCP_TRANSPORT", "streamable-http") == "stdio"
XIAO_URL = os.getenv("XIAO_URL", "http://192.168.4.1").rstrip("/")
DRIVE_SPEED = int(os.getenv("DRIVE_SPEED", "180"))      # PWM 0-255 for advance
PIVOT_SPEED = int(os.getenv("PIVOT_SPEED", "200"))      # PWM 0-255 for pivots
# Calibrate these on the venue floor (see README "Calibration"):
PIVOT_MS_PER_DEG = float(os.getenv("PIVOT_MS_PER_DEG", "6.5"))   # ms of pivot per degree
ADVANCE_MS_PER_CM = float(os.getenv("ADVANCE_MS_PER_CM", "25"))  # ms of driving per cm
HFOV_DEG = float(os.getenv("HFOV_DEG", "60"))           # camera horizontal field of view
DIST_K = float(os.getenv("DIST_K", "3500"))             # est_cm = DIST_K / marker_side_px (calibrate at 50cm)
CAM_ROTATE = int(os.getenv("CAM_ROTATE", "270"))        # 270 = 90 degrees counterclockwise
MARKER_ID = int(os.getenv("MARKER_ID", "0"))
SETTLE_S = float(os.getenv("SETTLE_S", "0.15"))         # pause before capture so frame isn't blurred

MAX_PIVOT_MS, MAX_ADVANCE_MS, RAM_MS = 800, 1000, 1200


# ------------------------------------------------------------ real tank ----
class RealTank:
    def __init__(self) -> None:
        self.http = httpx.Client(timeout=4.0)

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        r = self.http.get(f"{XIAO_URL}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def capture(self) -> bytes:
        r = self.http.get(f"{XIAO_URL}/capture")
        r.raise_for_status()
        return r.content

    def move(self, l: int, r: int, ms: int) -> dict[str, Any]:
        return self._get("/move", l=l, r=r, ms=ms)

    def ram(self, ms: int) -> dict[str, Any]:
        return self._get("/ram", ms=ms)

    def tof(self) -> int:
        return int(self._get("/tof").get("mm", -1))

    def stop(self) -> dict[str, Any]:
        return self._get("/stop")

    def turret(self, direction: str, ms: int) -> dict[str, Any]:
        return self._get("/turret", dir=direction, ms=ms)


# ------------------------------------------------------------- sim tank ----
class SimTank:
    """Tank at origin; pumpkin somewhere random. Units: cm, degrees."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.x, self.y, self.heading = 0.0, 0.0, 0.0       # heading 0 = +y
        ang = math.radians(random.uniform(0, 360))
        d = random.uniform(120, 220)
        self.px, self.py = d * math.sin(ang), d * math.cos(ang)
        self.knocked = False
        self.turret_ms = 0

    def _rel(self) -> tuple[float, float]:
        dx, dy = self.px - self.x, self.py - self.y
        dist = math.hypot(dx, dy)
        world = math.degrees(math.atan2(dx, dy))
        bearing = (world - self.heading + 180) % 360 - 180   # + = pumpkin to the right
        return bearing, dist

    def sitrep(self) -> dict[str, Any]:
        bearing, dist = self._rel()
        visible = (not self.knocked) and abs(bearing) <= HFOV_DEG / 2 and 15 <= dist <= 350
        tof = int(dist * 10) if (abs(bearing) <= 13 and not self.knocked) else 2500
        out: dict[str, Any] = {"visible": visible, "tof_mm": tof}
        if visible:
            noisy = dist * random.uniform(0.9, 1.1)
            out.update(bearing_deg=round(bearing + random.uniform(-2, 2), 1),
                       est_distance_cm=round(noisy, 0),
                       marker_px=round(DIST_K / noisy, 1))
        return out

    def move(self, l: int, r: int, ms: int) -> dict[str, Any]:
        slip = random.uniform(0.85, 1.15)
        if l < 0 < r or r < 0 < l:                          # pivot
            deg = ms / PIVOT_MS_PER_DEG * slip * (1 if l > 0 else -1)
            self.heading = (self.heading + deg) % 360
            return {"ran_ms": ms, "stopped_by": "time", "tof_mm": self.sitrep()["tof_mm"]}
        sign = 1 if (l + r) > 0 else -1
        cm = ms / ADVANCE_MS_PER_CM * slip
        bearing, dist = self._rel()
        stopped_by = "time"
        if sign > 0 and abs(bearing) <= 13 and not self.knocked and dist - cm < 25:   # firmware ToF stop
            cm, stopped_by = max(0.0, dist - 25), "tof"
        h = math.radians(self.heading)
        self.x += sign * cm * math.sin(h)
        self.y += sign * cm * math.cos(h)
        return {"ran_ms": ms, "stopped_by": stopped_by, "tof_mm": self.sitrep()["tof_mm"]}

    def ram(self, ms: int) -> dict[str, Any]:
        bearing, dist = self._rel()
        hit = (not self.knocked) and abs(bearing) <= 15 and dist <= ms / ADVANCE_MS_PER_CM + 10
        if hit:
            self.knocked = True
        h = math.radians(self.heading)
        cm = min(dist, ms / ADVANCE_MS_PER_CM)
        self.x += cm * math.sin(h)
        self.y += cm * math.cos(h)
        return {"ran_ms": ms, "sim_hit": hit}

    def stop(self) -> dict[str, Any]:
        return {"ok": True}

    def turret(self, direction: str, ms: int) -> dict[str, Any]:
        self.turret_ms = max(-700, min(700, self.turret_ms + (ms if direction == "right" else -ms)))
        return {"turret_offset_ms": self.turret_ms}


tank: RealTank | SimTank = SimTank() if SIM else RealTank()
_demo_motion_lock = threading.Lock()


# --------------------------------------------------------------- vision ----
def see(jpeg: bytes) -> dict[str, Any]:
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return {"visible": False, "error": "bad image"}
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    if CAM_ROTATE in rot:
        img = cv2.rotate(img, rot[CAM_ROTATE])
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    out: dict[str, Any] = {"visible": False}
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            if int(i) != MARKER_ID:
                continue
            pts = c.reshape(4, 2)
            cx = float(pts[:, 0].mean())
            side = float(np.mean([np.linalg.norm(pts[k] - pts[(k + 1) % 4]) for k in range(4)]))
            w = img.shape[1]
            out = {"visible": True,
                   "bearing_deg": round((cx / w - 0.5) * HFOV_DEG, 1),
                   "est_distance_cm": round(DIST_K / max(side, 1.0), 0),
                   "marker_px": round(side, 1)}
            cv2.aruco.drawDetectedMarkers(img, [c], np.array([[i]]))
            break
    cv2.putText(img, str(out), (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    cv2.imwrite("last.jpg", img)
    return out


def sitrep_now() -> dict[str, Any]:
    if isinstance(tank, SimTank):
        return tank.sitrep()
    time.sleep(SETTLE_S)
    out = see(tank.capture())
    try:
        out["tof_mm"] = tank.tof()
    except Exception:
        out["tof_mm"] = -1
    return out


# ------------------------------------------------------------ MCP tools ----
server = MCPServer(
    name="warmo-patrol",
    instructions=(("SIMULATION MODE: these tools operate a fake tank, not the physical vehicle. " if SIM else "REAL TANK MODE: these tools control the physical vehicle. ")
                  + "WARMO is a camera scout for room patrol and scene observations. "
                  "Use look to observe surroundings before and after each short movement. "
                  "Bearing is degrees: negative = a visible landmark is LEFT, positive = RIGHT. "
                  "Every move is short and self-stops. Save patrol observations and completed actions to mission memory."),
)


@server.tool()
def connection_status() -> dict[str, Any]:
    """Read-only connection check. Explicitly identifies SIM/REAL and WARMO firmware readiness."""
    if isinstance(tank, SimTank):
        return {"sim": True, "mode": "SIM", "ready": True, "xiao_url": None,
                "firmware": {"name": "WARMO", "protocol": 1, "camera_ok": True,
                             "tof_ok": False, "pwm": True, "motors_on": False,
                             "simulated": True}}
    try:
        status = tank._get("/status")
        ready = (status.get("name") == "WARMO" and status.get("protocol") == 1
                 and status.get("camera_ok") is True and status.get("pwm") is True
                 and status.get("motors_on") is False)
        return {"sim": False, "mode": "REAL", "ready": ready,
                "xiao_url": XIAO_URL, "firmware": status}
    except Exception as error:
        return {"sim": False, "mode": "REAL", "ready": False,
                "xiao_url": XIAO_URL, "firmware": None, "error": str(error)}


@server.tool()
def demo_move(direction: str, ms: int = 200) -> dict[str, Any]:
    """One gentle demo pulse: forward/backward/left/right, 50% PWM, 50-250 ms, then stop.
    Announce the action and ensure the owner is ready before using on the real tank."""
    directions = {"forward": (128, 128), "backward": (-128, -128),
                  "left": (-128, 128), "right": (128, -128)}
    with _demo_motion_lock:
        if direction not in directions:
            tank.stop()
            raise ValueError("Unknown direction: stopped. Use forward/backward/left/right.")
        duration = max(50, min(250, int(ms)))
        left, right = directions[direction]
        try:
            result = tank.move(left, right, duration)
        finally:
            tank.stop()
        return {"sim": SIM, "mode": "SIM" if SIM else "REAL", "direction": direction,
                "requested_ms": duration, "duty": 128, **result}


@server.tool()
def get_sitrep() -> dict[str, Any]:
    """Read a compact patrol status summary from the camera and distance sensor.
    Reports landmark visibility, bearing and estimated distance when available,
    plus tof_mm (distance straight ahead, -1 if the optional sensor is unavailable)."""
    return sitrep_now()


def _sim_scene() -> str:
    assert isinstance(tank, SimTank)
    rooms = ["a door", "a table with laptops", "a window", "a person waving", "a hallway", "a wall",
             "a snack table", "a chair"]
    return rooms[int(tank.heading // 45) % len(rooms)]


def _sim_view() -> bytes:
    import cv2
    import numpy as np

    assert isinstance(tank, SimTank)
    img = np.full((240, 320, 3), 60, np.uint8)
    thing = _sim_scene()
    cv2.putText(img, "SIM PATROL CAMERA", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(img, f"heading {tank.heading:.0f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(img, f"you see: {thing}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    ok, jpg = cv2.imencode(".jpg", img)
    return jpg.tobytes()


@server.tool()
def look() -> Any:
    """Take one camera photo plus tof_mm (laser distance straight ahead, -1 if no sensor).
    Desktop receives a viewable image; HTTP agents receive a base64 JPEG in JSON.
    Used by patrol mode so Claude can see the room."""
    import base64

    if isinstance(tank, SimTank):
        jpeg, tof = _sim_view(), 2500
    else:
        time.sleep(SETTLE_S)
        jpeg = tank.capture()
        try:
            tof = tank.tof()
        except Exception:
            tof = -1
    if CAM_ROTATE:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        if img is not None and CAM_ROTATE in rot:
            jpeg = cv2.imencode(".jpg", cv2.rotate(img, rot[CAM_ROTATE]))[1].tobytes()
    with open("last.jpg", "wb") as f:
        f.write(jpeg)
    encoded = base64.b64encode(jpeg).decode()
    metadata = {"sim": SIM, "tof_mm": tof}
    if isinstance(tank, SimTank):
        metadata["sim_caption"] = f"SIM patrol camera, heading {tank.heading:.0f}: you see {_sim_scene()}"
    if DESKTOP_STDIO:
        # Desktop clients need an MCP image block; brain.py still receives its
        # original JSON/base64 response over the existing HTTP transport.
        import json

        return [
            TextContent(type="text", text=json.dumps(metadata)),
            ImageContent(type="image", data=encoded, mimeType="image/jpeg"),
        ]
    return {**metadata, "image_jpeg_b64": encoded}


@server.tool()
def pivot(direction: str, degrees: float) -> dict[str, Any]:
    """Spin the tank in place. direction: 'left' or 'right'. degrees: 5-120."""
    deg = max(5.0, min(120.0, abs(float(degrees))))
    ms = int(min(MAX_PIVOT_MS, deg * PIVOT_MS_PER_DEG))
    s = PIVOT_SPEED
    l, r = (-s, s) if direction.lower().startswith("l") else (s, -s)
    res = tank.move(l, r, ms)
    return {"pivoted": direction, "requested_deg": deg, **res}


@server.tool()
def advance(cm: float) -> dict[str, Any]:
    """Drive straight forward cm (5-40). Firmware stops early if something is within 25cm."""
    dist = max(5.0, min(40.0, abs(float(cm))))
    ms = int(min(MAX_ADVANCE_MS, dist * ADVANCE_MS_PER_CM))
    res = tank.move(DRIVE_SPEED, DRIVE_SPEED, ms)
    return {"requested_cm": dist, **res}


def ram() -> dict[str, Any]:
    """Full-speed charge straight ahead for ~1 second, ignoring the 25cm safety stop.
    Only use when the pumpkin is centered (|bearing| < 10) and closer than ~40cm."""
    return {"rammed": True, **tank.ram(RAM_MS)}


@server.tool()
def stop() -> dict[str, Any]:
    """Stop all motors immediately."""
    return tank.stop()


@server.tool()
def turret(direction: str, ms: int = 300) -> dict[str, Any]:
    """DISABLED: returns a truthful no-op response without contacting any motor or hardware."""
    return {"ok": True, "disabled": True, "moved": False, "ran_ms": 0,
            "reason": "Turret movement is disabled; no hardware command was sent."}


@server.tool()
def sim_reset() -> dict[str, Any]:
    """SIM only: restart the patrol simulation at its starting position."""
    if isinstance(tank, SimTank):
        tank.reset()
        return {"ok": True}
    return {"ok": False, "error": "not in SIM mode"}


if __name__ == "__main__":
    if data_dir := os.getenv("WARMO_DATA_DIR"):
        os.makedirs(data_dir, exist_ok=True)
        os.chdir(data_dir)
    # stdout carries JSON-RPC when launched by Claude Desktop.
    print(f"Warmo MCP | SIM={SIM} | XIAO_URL={XIAO_URL}", file=sys.stderr, flush=True)
    if DESKTOP_STDIO:
        # On Windows, loading NumPy's native extension inside the active
        # stdio event loop can stall. Load vision libraries before the loop.
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))
