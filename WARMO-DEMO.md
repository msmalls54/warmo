# WARMO: today's demo

Use one controller at a time. Keep extra browser camera tabs closed. Use **SIM for the restart proof** while the remaining physical checks are resolved.

Desktop is configured for **REAL**, `192.168.137.28`, with drive/pivot duty 128 (50%). Confirm its current connection with the read-only prompt below. The separate `new`/`go` default remains **SIM**.

Turret control is disabled. Its registered tool returns a no-op without contacting the tank; the patrol prompt and rules use track turns instead. Camera images use `CAM_ROTATE=270`, or **90 degrees counter-clockwise**, in both prepared controller paths.

## Simple pumpkin demo

1. Put the pumpkin in the camera's view. Start a fresh real pumpkin mission; Warmo describes the actual pumpkin and approaches with short track moves, stopping nearby.
2. After a printed saved step includes the pumpkin sighting, stop the brain with Ctrl-C. Leave the tank powered.
3. Run `go` on the same mission. Show `RESUMING`, the saved pumpkin sighting, and continued progress.

The memory/restart path is verified. Actual pumpkin recognition and the standalone brain's physical pumpkin approach still need a live check. Keep the current patrol/vision path; do not enable the legacy ArUco/ram mode. The saved goal and phase now reach the model every turn, including after old logs roll over.

| Part | Job |
|---|---|
| Claude Desktop | Chooses tools in a conversation; uses Claude Desktop's model. |
| `new` / `go` and `brain.py` | Separate patrol loop using OpenRouter Opus 5.5, with rules fallback. Ctrl-C stops this process. |
| Manufact mcp-use v2 | Exposes camera, bounded movement, and mission-memory tools. |
| Terse `Mission` | Persists a named mission's goal, phase, step count, observations, action log, estimated heading and travel. |

## Thirty-second explanation

> Claude gets eyes and short movement tools through Manufact's MCP server. Terse stores the mission's completed steps, sightings, estimated heading and travel outside the brain process. I'll stop the patrol, restart it, and show the same mission resuming with its observations and next step number. That is the continuity the demo proves.

**Why Terse instead of conversation history or a JSON file?** Fresh clients access a stable `Mission` identity through persisted fields and actor methods. That state survived brain, server, and VM restarts. A JSON file could also store these fields; this demo demonstrates working actor recovery, not production scale or a universal advantage over files.

## Two-minute restart demo

From PowerShell in this project:

1. **0:00–0:20:** Explain the roles above. State that this proof uses simulated camera scenes and motors.
2. **0:20–1:00:** Run:

   ```powershell
   .\new -Sim -MissionId warmo-stage-demo
   ```

   This resets only that named mission. Let several completed `step N` lines appear; each prints after its Terse write.
3. **1:00–1:15:** After a saved step, press **Ctrl-C**. Note the last saved step number.
4. **1:15–2:00:** Run:

   ```powershell
   .\go
   ```

   Point to the same mission ID, `RESUMING mission`, retained step count, increased resume count, and prior sightings printed one per line. After the next saved step continues the count, press **Ctrl-C** again. Do not run `new` again during this beat.

Fresh Linux SIM/rules rehearsal: **3 → resume → 5 saved steps**, with three prior sightings printed separately. [Fresh receipt](C:/Users/Small/hw-test/terse-vm/warmo-terse-rehearsal-20261031T211757Z/verification.json). Windows `new`/`go` wrappers were tested earlier, **16 → 28**; they were not rerun in that fresh rehearsal. [Wrapper receipt](C:/Users/Small/hw-test/terse-vm/new-go-patrol-proof.json). State also survived [server](C:/Users/Small/hw-test/terse-vm/server-restart-proof.json) and [VM](C:/Users/Small/hw-test/terse-vm/vm-restart-proof.json) restarts.

## Claude Desktop starting prompt

Desktop control is a separate demonstration: it does not start `brain.py`, and a new chat does not itself execute the `new`/`go` restart sequence. Claude was restarted and its 12 tools loaded in REAL mode. [Current Desktop receipt](C:/Users/Small/hw-test/warmo-desktop-runtime/manufact-v2/desktop-real-restart-receipt-20261031T212125335734Z.json). A read-only client using that same configured entry also retrieved the explicitly SIM rehearsal mission at step 5, resume count 1. This confirms the MCP-to-Terse memory bridge; it is not a physical patrol proof. Paste:

```text
Use only WARMO. First make these read-only calls:
connection_status {}
mission_state {"mission_id":"warmo-desktop-demo"}
look {}

Report SIM versus REAL, the saved mission step (or an empty mission), and whether the photo is clear and aimed usefully. Do not move or change memory until Mike explicitly says “go.” If the connection or camera check fails, report it and wait.

After “go,” call mission_start with mission_id="warmo-desktop-demo" and goal="Patrol the demo area and report objects." Announce each action. Use only demo_move with direction="forward", "backward", "left", or "right" and ms=150, one pulse at a time, then stop {} and look {}. If a reply times out, stop the sequence and report uncertainty; do not assume the movement failed.

After each acknowledged completed step, use mission_record_patrol with that same mission_id, the action, and what you actually saw. Use turned_deg=0 and moved_cm=0 when changes were not measured; do not invent distance or heading changes. Do not start another controller. Roast objects, never people's appearance. For the pumpkin mission, find and approach without touching it.
```

## Current physical status and limits

- Mike confirmed floor forward/backward travel and approved live track/pivot batches. Turret control is now disabled by request after the right-direction hardware failure. ToF was explicitly skipped: there is no distance-sensor obstacle protection. [Current floor receipt](C:/Users/Small/hw-test/hardware-checks/current-floor-status-20261031.json).
- Real camera transport and an upright 270-degree host rotation were used during the live MCP patrol. The current camera view must still show the actual pumpkin before the target demo. An earlier HTTP pause recovered after closing a camera tab; the cause remains unproven. [HTTP diagnosis](C:/Users/Small/hw-test/warmo-firmware/wifi-http-diagnosis-20261031.md).
- Terse stores **application state**, not the model's hidden thoughts. The actor retains 12 sightings and 20 actions; `state()` exposes the latest eight actions. Heading and travel are estimates, not a measured map.
- A movement acknowledgement and its memory write are not atomic. A lost acknowledgement can repeat a bounded pulse on retry. The restart proof deliberately stops after a confirmed saved step. HTTP `stop` can wait behind an active firmware handler; disconnecting the separate motor battery is the physical stop.
