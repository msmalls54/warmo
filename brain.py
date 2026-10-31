"""Warmo's brain: a stateless loop. Each step: read mission memory (Terse) -> look -> one move -> save.

  POLICY=llm   (default) Claude decides via mcp-use MCPAgent + the tank MCP tools
  POLICY=rules deterministic fallback (no API key needed). Also used automatically if an LLM step fails.

Run:  uv run brain.py            (needs `durable-actors dev` and `tank_mcp.py` running)
      uv run brain.py --reset    (wipe mission memory and start fresh)
Demo: Ctrl-C this (or the Terse dev server) mid-hunt, restart it, watch it resume.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import time
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("MCP_USE_ANONYMIZED_TELEMETRY", "false")

import logging  # noqa: E402

from mcp_use import MCPAgent, MCPClient  # noqa: E402

for _n in ("mcp_use", "mcp", "httpx"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)
from pydantic import BaseModel, Field  # noqa: E402

from generated import actors  # noqa: E402

MISSION_ID = os.getenv("MISSION_ID", "warmo-1")
GOAL = os.getenv("GOAL", "Find the pumpkin with ArUco marker 0 and ram it.")
POLICY = os.getenv("POLICY", "llm")
MODEL = os.getenv("MODEL", "claude-haiku-5-5")
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
MAX_STEPS = int(os.getenv("MAX_STEPS", "200"))
STEP_PAUSE_S = float(os.getenv("STEP_PAUSE_S", "0.3"))

CLIENT_CFG = {"mcpServers": {"tank": {"url": MCP_URL}}}


# --------------------------------------------------- durable call helper ----
def durable(fn: Callable[..., Any], *args: Any, wait_s: float = 90) -> Any:
    """Call a Terse actor method, retrying while the Terse server restarts."""
    deadline = time.time() + wait_s
    warned = False
    while True:
        try:
            return fn(*args)
        except Exception as e:  # ActorInvocationError, connection errors during restart
            if time.time() > deadline:
                raise
            if not warned:
                print(f"  ...mission memory (Terse) unavailable, waiting for it to come back ({type(e).__name__})")
                warned = True
            time.sleep(2)


# --------------------------------------------------------- step result ----
class StepReport(BaseModel):
    action: str = Field(description="what you did, e.g. 'pivot right 45', 'advance 30', 'ram'")
    phase: str = Field(description="searching | approaching | ramming | done")
    visible: bool = Field(description="was the marker visible in the get_sitrep you called")
    bearing_deg: float = Field(default=0.0, description="bearing from get_sitrep (0 if not visible)")
    distance_cm: float = Field(default=0.0, description="est_distance_cm from get_sitrep (0 if not visible)")
    turned_deg: float = Field(default=0.0, description="degrees pivoted this step (0 if none)")


# ----------------------------------------------------------- rules policy ----
async def step_rules(st: dict[str, str]) -> StepReport:
    client = MCPClient.from_dict(CLIENT_CFG)
    s = await client.create_session("tank")

    async def call(name: str, **args: Any) -> dict[str, Any]:
        r = await s.call_tool(name, args)
        return json.loads(r.content[0].text) if r.content else {}

    try:
        sit = await call("get_sitrep")
        step, last_seen = int(st["step_no"]), int(st["last_seen_step"])
        if sit.get("visible"):
            b, d = float(sit["bearing_deg"]), float(sit["est_distance_cm"])
            tof = int(sit.get("tof_mm", -1))
            if abs(b) > 8:
                deg = min(abs(b), 60)
                await call("pivot", direction="right" if b > 0 else "left", degrees=deg)
                return StepReport(action=f"pivot {'right' if b > 0 else 'left'} {deg:.0f} to center target",
                                  phase="approaching", visible=True, bearing_deg=b, distance_cm=d)
            if d <= 40 or 0 < tof <= 350:
                await call("ram")
                return StepReport(action="RAM", phase="done", visible=True, bearing_deg=b, distance_cm=d)
            cm = max(10.0, min(40.0, d - 30))
            await call("advance", cm=cm)
            return StepReport(action=f"advance {cm:.0f}cm", phase="approaching",
                              visible=True, bearing_deg=b, distance_cm=d)
        # not visible
        if last_seen >= 0 and step - last_seen <= 2:
            lb = float(st["last_bearing_deg"])
            direction = "right" if lb > 0 else "left"
            await call("pivot", direction=direction, degrees=30)
            return StepReport(action=f"lost it, pivot {direction} 30 toward last sighting",
                              phase="searching", visible=False, turned_deg=30)
        if float(st["search_turned_deg"]) < 360:
            await call("pivot", direction="right", degrees=45)
            return StepReport(action="search: pivot right 45", phase="searching", visible=False, turned_deg=45)
        await call("advance", cm=30)
        return StepReport(action="full circle, nothing: advance 30cm to a new spot", phase="searching",
                          visible=False, turned_deg=-float(st["search_turned_deg"]))
    finally:
        await client.close_all_sessions()


# ------------------------------------------------------------- LLM policy ----
SYSTEM = """You are the brain of Warmo, an autonomous tank hunting a pumpkin marked with an ArUco tag.
Each turn you do exactly this: call get_sitrep ONCE, then call AT MOST ONE of pivot / advance / ram, then stop.
Bearing: negative = target is LEFT, positive = RIGHT. Moves are short and self-stopping.
Policy:
- Visible and |bearing| > 8: pivot toward it by about |bearing| degrees.
- Visible, roughly centered, est_distance_cm > 40: advance about (distance - 30) cm, max 40.
- Visible, centered, est_distance_cm <= 40 (or tof_mm <= 350): ram, and set phase to done.
- Not visible but seen in the last 2 steps: pivot 30 toward where it was last seen.
- Not visible: pivot right 45 to scan. After a full 360 with nothing, advance 30 to a new spot.
Never call sim_reset or turret. Then report what you did."""


async def step_llm(st: dict[str, str]) -> StepReport:
    from langchain_anthropic import ChatAnthropic

    client = MCPClient.from_dict(CLIENT_CFG)
    agent = MCPAgent(llm=ChatAnthropic(model=MODEL, temperature=0, max_tokens=1024), client=client,
                     max_steps=4, memory_enabled=False, system_prompt=SYSTEM,
                     disallowed_tools=["sim_reset", "turret"])
    prompt = ("Mission memory (from durable storage):\n" + json.dumps(st, indent=1) +
              "\nTake your one turn now.")
    try:
        rep = await agent.run(prompt, output_schema=StepReport)
        return rep if isinstance(rep, StepReport) else StepReport.model_validate_json(str(rep))
    finally:
        await client.close_all_sessions()


# ----------------------------------------------------------- PATROL MODE ----
MODE = os.getenv("MODE", "patrol")          # patrol (roam + look + narrate) | hunt (pumpkin + ArUco)
PATROL_GOAL = os.getenv("PATROL_GOAL", "Patrol the hackathon floor, look around, report what you see.")
SAFE_ADVANCE_MM = 600                        # only drive forward if ToF says >60cm clear (or no ToF)

PATROL_SYSTEM = """You are WARMO, an autonomous AI tank on patrol at a Halloween hackathon in San Francisco
(a self-driving vehicle like Waymo, but armored). Each turn you get ONE camera photo, the laser distance
straight ahead, and your durable mission memory (what you've already seen and where).
Reply with ONLY a JSON object, no prose:
{"saw": "<5-12 words: the most interesting thing in this photo>",
 "say": "<one short line of in-character radio chatter, dry and funny, about what you see>",
 "action": "pivot" | "advance" | "hold",
 "direction": "left" | "right",
 "amount": <pivot: degrees 20-90 | advance: cm 10-40 | hold: 0>}
Follow the mission goal in memory before generic exploration.
For visibly clear, open floor, prefer a 40cm straight drive (up to one second),
then take another photo. Use smaller advances near objects or when clearance is uncertain.
Rules: explore new directions you haven't looked at recently (use est_heading_deg and sightings).
Only advance if tof_mm > 600 or tof_mm == -1 AND the photo shows open floor ahead. Never advance at people
closer than ~1m. Use track pivots to look in a new direction."""

PATROL_SYSTEM += "\nRoast objects, never people's appearance."


def _parse_json(text: str) -> dict[str, Any]:
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1]) if s >= 0 and e > s else {}


class PatrolLinkError(RuntimeError):
    """The tank did not return a successful, usable MCP response."""


class PatrolProviderError(RuntimeError):
    """The pre-move LLM decision failed; rules may safely decide this step."""


def _patrol_tool_result(result: Any, tool: str) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise PatrolLinkError("Tank MCP returned an error")
    payload: dict[str, Any] = {}
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payload.update(structured)
    for part in getattr(result, "content", []) or []:
        if getattr(part, "type", None) == "text":
            decoded = json.loads(part.text)
            if not isinstance(decoded, dict):
                raise PatrolLinkError("Tank MCP returned non-object JSON")
            payload.update(decoded)
        elif getattr(part, "type", None) == "image":
            if part.mimeType != "image/jpeg":
                raise PatrolLinkError("Tank camera returned an unsupported image type")
            payload["image_jpeg_b64"] = part.data
    if payload.get("error") or payload.get("ok") is False or payload.get("success") is False:
        raise PatrolLinkError("Tank tool reported a failure")
    for field in ("status_code", "http_status"):
        if field in payload and int(payload[field]) >= 400:
            raise PatrolLinkError("Tank HTTP request failed")
    if tool == "look":
        if not isinstance(payload.get("image_jpeg_b64"), str) or not payload["image_jpeg_b64"]:
            raise PatrolLinkError("Tank camera returned no photo")
        payload["tof_mm"] = int(payload.get("tof_mm", -1))
    return payload


def _rules_sighting(view: dict[str, Any], st: dict[str, str]) -> str:
    if view.get("sim") is True and str(view.get("sim_caption", "")).strip():
        caption = str(view["sim_caption"]).strip()
        return caption if caption.upper().startswith("SIM") else f"SIM: {caption}"
    label = "SIM camera frame" if view.get("sim") is True else "Camera frame"
    tof = int(view.get("tof_mm", -1))
    distance = f"ToF {tof}mm" if tof >= 0 else "ToF unavailable"
    return f"{label}, heading {st.get('est_heading_deg', '0')}deg; {distance} (rules, no object recognition)"


async def patrol_step(st: dict[str, str], use_llm: bool) -> tuple[str, str, str, float, float]:
    """Returns (action_desc, saw, say, turned_deg, moved_cm)."""
    st = dict(st)
    client: Any = None
    session: Any = None

    async def close_link() -> None:
        nonlocal client, session
        previous, client, session = client, None, None
        if previous is not None:
            try:
                await asyncio.wait_for(previous.close_all_sessions(), timeout=1)
            except Exception:
                pass  # A broken link must not make cleanup abort the retry.

    async def call(name: str, **args: Any) -> dict[str, Any]:
        nonlocal client, session
        frozen = copy.deepcopy(args)
        while True:
            try:
                if session is None:
                    client = MCPClient.from_dict(CLIENT_CFG)
                    session = await client.create_session("tank")
                response = await session.call_tool(name, copy.deepcopy(frozen))
                return _patrol_tool_result(response, name)
            except Exception:
                print("[link lost] retrying...", flush=True)
                await close_link()
                await asyncio.sleep(2)

    try:
        view = await call("look")
        tof = int(view.get("tof_mm", -1))
        step = int(st["step_no"])
        disabled_turret = False
        if use_llm:
            try:
                import anthropic
                mem = {k: st[k] for k in ("step_no", "est_heading_deg", "driven_cm", "sightings", "recent_log", "resumes")}
                mem.update(goal=st.get("goal") or PATROL_GOAL, phase=st.get("phase") or "patrolling")
                router_key = os.getenv("OPENROUTER_API_KEY", "").strip()
                use_router = bool(router_key) and "..." not in router_key and not router_key.startswith("YOUR_")
                client_options: dict[str, Any] = {}
                request_options: dict[str, Any] = {}
                model = MODEL
                if use_router:
                    # SDK 2.x permits only one explicit credential source.
                    # An explicit bearer token also bypasses ANTHROPIC_API_KEY.
                    client_options = {"base_url": "https://openrouter.ai/api", "auth_token": router_key}
                    model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-opus-5.5")
                    request_options["extra_body"] = {"output_config": {"effort": "low"}}
                provider = anthropic.AsyncAnthropic(**client_options)
                try:
                    msg = await provider.messages.create(
                        model=model, max_tokens=2048, system=PATROL_SYSTEM,
                        messages=[{"role": "user", "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": view["image_jpeg_b64"]}},
                            {"type": "text", "text": f"tof_mm={tof}\nmission memory: {json.dumps(mem)}\nYour turn."}]}],
                        **request_options)
                finally:
                    await provider.close()
                decision_text = "\n".join(block.text for block in msg.content
                                          if getattr(block, "type", "text") == "text" and isinstance(getattr(block, "text", None), str))
                d = _parse_json(decision_text)
                if isinstance(d, dict) and d.get("action") == "turret":
                    # A stale model response must neither actuate nor claim a turn.
                    d.update(action="hold", amount=0, say="Holding position; turret movement is disabled.")
                    disabled_turret = True
                if not isinstance(d, dict) or d.get("action") not in ("pivot", "advance", "hold"):
                    raise ValueError("Invalid patrol decision")
                float(d.get("amount", 0) or 0)
            except Exception as e:
                raise PatrolProviderError(type(e).__name__) from e
        else:  # rules: sweep with track pivots, advance when clear
            if step % 3 == 2 and (tof > SAFE_ADVANCE_MM or tof == -1):
                d = {"saw": "", "say": "Path clear, moving up.", "action": "advance", "direction": "", "amount": 40 if tof != -1 else 20}
            else:
                d = {"saw": "", "say": "Scanning.", "action": "pivot", "direction": "right", "amount": 45}
            d["saw"] = _rules_sighting(view, st)

        action = str(d.get("action", "hold"))
        direction = "left" if str(d.get("direction", "right")).lower().startswith("l") else "right"
        amount = float(d.get("amount", 0) or 0)
        turned, moved = 0.0, 0.0
        if action == "pivot":
            amount = max(20, min(90, amount or 45))
            await call("pivot", direction=direction, degrees=amount)
            turned = amount if direction == "right" else -amount
            desc = f"pivot {direction} {amount:.0f}°"
        elif action == "advance" and (tof > SAFE_ADVANCE_MM or tof == -1):
            # Vision can establish open floor without the optional ToF. Rules have
            # no object recognition, so retain their shorter blind-motion limit.
            limit = 40 if tof != -1 or use_llm else 20
            amount = max(10, min(limit, amount or 20))
            res = await call("advance", cm=amount)
            moved = amount if res.get("stopped_by") != "tof" else amount / 2
            desc = f"advance {amount:.0f}cm" + (" (stopped: obstacle)" if res.get("stopped_by") == "tof" else "")
        else:
            desc = ("hold (turret disabled)" if disabled_turret else
                    "hold" if action != "advance" else "hold (advance blocked: too close)")
        return desc, str(d.get("saw", "")), str(d.get("say", "")), turned, moved
    finally:
        await close_link()


def run_patrol(mission: Any) -> None:
    while True:
        st = durable(mission.state)
        if int(st["step_no"]) >= MAX_STEPS:
            print("\n--- step cap reached, patrol complete ---")
            break
        t0 = time.time()
        use_llm = POLICY == "llm"
        try:
            desc, saw, say, turned, moved = asyncio.run(patrol_step(st, use_llm))
            who = POLICY
        except PatrolProviderError as e:
            print(f"  (LLM step failed: {e} -> rules for this step)")
            try:
                desc, saw, say, turned, moved = asyncio.run(patrol_step(st, False))
                who = "rules"
            except Exception as fallback_error:
                print(f"[patrol stopped] Rules failed ({type(fallback_error).__name__}); step not recorded.")
                return
        except Exception as e:
            print(f"[patrol stopped] Step failed ({type(e).__name__}); step not recorded.")
            return
        n = durable(mission.record_patrol, desc, saw, turned, moved)
        print(f"step {n:>2} [{who:5}] {time.time() - t0:4.1f}s | {desc:<22} | {say}" + (f"  (saw: {saw})" if saw else ""))
        time.sleep(STEP_PAUSE_S)


# -------------------------------------------------------------------- main ----
def main() -> None:
    mission = actors.Mission.get(MISSION_ID)
    if "--reset" in sys.argv:
        print(durable(mission.reset))
    goal = PATROL_GOAL if MODE == "patrol" else GOAL
    started = durable(mission.start, goal)
    st = durable(mission.state)
    if str(started).startswith("resuming"):
        print(f"\n=== RESUMING mission '{MISSION_ID}' from durable memory (resume #{int(st['resumes'])}) ===")
        print(f"    phase={st['phase']}  step={st['step_no']}  heading≈{st.get('est_heading_deg')}°  "
              f"driven≈{st.get('driven_cm')}cm")
        if MODE == "hunt":
            print(f"    last seen at step {st['last_seen_step']} (bearing {st['last_bearing_deg']}°, "
                  f"~{st['last_distance_cm']}cm)")
        if st.get("sightings"):
            if MODE == "patrol":
                print("    already seen:")
                for sighting in st["sightings"].split(" | "):
                    print(f"      {sighting}")
            else:
                print(f"    already seen: {st['sightings']}")
        print(f"    recent: {st['recent_log']}\n")
    else:
        print(f"\n=== NEW mission '{MISSION_ID}': {goal} (mode={MODE}, policy={POLICY}) ===\n")

    if MODE == "patrol":
        run_patrol(mission)
        return

    while True:
        st = durable(mission.state)
        if st["phase"] == "done":
            print("\n*** MISSION COMPLETE: target rammed ***")
            break
        if int(st["step_no"]) >= MAX_STEPS:
            print("\n--- step cap reached, stopping ---")
            break
        t0 = time.time()
        try:
            rep = asyncio.run(step_llm(st) if POLICY == "llm" else step_rules(st))
            who = POLICY
        except Exception as e:
            print(f"  (LLM step failed: {type(e).__name__}: {str(e)[:120]} -> using rules for this step)")
            rep = asyncio.run(step_rules(st))
            who = "rules"
        n = durable(mission.record, rep.action, rep.phase, rep.visible, rep.bearing_deg,
                    rep.distance_cm, rep.turned_deg)
        seen = f"seen {rep.bearing_deg:+.0f}° ~{rep.distance_cm:.0f}cm" if rep.visible else "not visible"
        print(f"step {n:>2} [{who:5}] {time.time() - t0:4.1f}s | {seen:<22} | {rep.action}  -> {rep.phase}")
        time.sleep(STEP_PAUSE_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[brain killed] tank stops on its own (every move self-terminates). Restart brain.py to resume.")
