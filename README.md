# WARMO — persistent mission memory for a real AI tank

**Stop the brain. Keep the mission.**

**[Watch the demo](https://youtu.be/PsJ0TtAfg-c)** — The Brain Restarts. The Mission Remembers.

Waymo drives you home. **Warmo patrols.**

WARMO connects Claude to a modified Heng Long M1A2 Abrams through **Manufact mcp-use v2**.
Claude sees the camera, chooses a short track move, and saves what happened to a **Terse Mission actor**.
A fresh chat or brain process can load that same mission's goal, sightings and progress before continuing.

**Live demo:** the supplied Claude Desktop captures report a fresh chat resuming `warmo-3` through Terse
and completing the run at **27 logged steps**. The earlier capture reports a saved pumpkin sighting and
notes about inverted turns and rightward drift. Distances and bearings in these reports are controller estimates.

[Demo and operator guide](WARMO-DEMO.md) · [Run the Manufact v2 connector](manufact-v2/README.md)

Real camera transport and track movement have been tested on the XIAO ESP32-S3 Sense. Turret control is
currently disabled; the optional ToF sensor was skipped. Use the demo guide's supervised movement procedure.

```
XIAO ESP32-S3 <-- HTTP --> Python tank tools <-- stdio --> Manufact mcp-use v2
                                                               ^ MCP
                                                Claude Desktop or brain.py
                                                               ^ read/write
                                                  Terse: named Mission actor
```

## Files
| File | What it is |
|---|---|
| `src/actors.py` | Terse `Mission` actor: phase, step count, heading, sightings, action log |
| `tank_mcp.py` | Scout/patrol MCP tools: `look`, `get_sitrep`, `pivot`, `advance`, `stop`, plus connection/demo helpers. Registered `turret` is a disabled no-op. `SIM=1` = fake tank |
| `manufact-v2/` | Portable Manufact mcp-use 2.12.2 connector: stdio for Desktop or local HTTP for the patrol loop; delegates tank behavior to the Python tools |
| `brain.py` | Patrol agent loop. `POLICY=llm` or `rules`; provider failures fall back to rules, link failures retry the same tool call |
| `new.ps1` / `go.ps1` | Prepared Windows launchers: fresh mission / resume saved mission |
| `firmware/warmo_firmware/warmo_firmware.ino` | XIAO HTTP firmware: `/capture /move /ram /tof /stop /turret` |
| `aruco_id0_print.png` | Marker for hunt mode (print ~10cm, matte, tape to a flat card) |

## Windows demo prepared on this laptop

See [WARMO-DEMO.md](WARMO-DEMO.md) for the short explanation, presentation sequence, Claude starting prompt, and current hardware limits.
These Windows launchers use the prepared runtime under `~/hw-test` on the demo laptop. For a fresh clone,
use the [portable v2 setup](manufact-v2/README.md); the external VM and private laptop runtime are not in Git.

The patrol demo uses **Manufact mcp-use 2.12.2** and a real **Terse 0.14.6 Mission actor**.
It looks, narrates, chooses a bounded track move or hold, and saves the completed step. The MCP inspector
exposes scout/patrol tools; `ram` is not registered. The patrol prompt says: "Roast objects, never people's appearance."

Runtime packages, firmware builds, private Wi-Fi configuration, and the isolated Ubuntu VM live under
`C:\Users\Small\hw-test`, outside OneDrive. The VM uses existing Windows hypervisor support; WSL installation,
a Windows restart, and a direct Anthropic API key are not needed for this prepared demo.

From PowerShell in this project:

```powershell
.\new      # start a fresh patrol mission
# Press Ctrl-C to stop the brain, then:
.\go       # resume the same saved mission; never resets it
```

The launchers start/reuse the local runtime and select **SIM by default**. They keep the selected mode and
mission ID in a private settings file outside OneDrive. `new` resets only that selected mission; `go` resumes
it and refuses a missing or incompatible mission. `MAX_STEPS=200` keeps the patrol running during the pitch.
On resume, saved sightings print one per line. A failed look/move prints `[link lost] retrying...`, waits two
seconds, and retries the same tool and arguments without recording a failed step. Ctrl-C remains available.

The approved OpenRouter credential is stored only in
`C:\Users\Small\hw-test\warmo-desktop-runtime\openrouter.env`. The launcher reads it privately and uses
`anthropic/claude-opus-5.5` for `POLICY=llm`. Without a usable provider key, it uses rules. Rules mode reports
SIM scene captions or camera/heading/distance facts; it does not claim to recognize real objects.

Physical movement and a durable write are not one transaction: after an unexpected real-world interruption,
check the vehicle's position before resuming. A lost movement acknowledgement can cause a repeated bounded
pulse on retry. The verified restart test interrupts after a confirmed saved step.

The earlier fixed-sequence bench test remains available:

```powershell
.\warmo.ps1 -Action Status
.\warmo.ps1 -Action SimDemo -MissionId my-sim-demo
```

Its six-step plan checks the opening camera view/memory, announces forward/backward and left/right pivots,
then takes a closing photo. Drive requests use 50% PWM for 200ms; pivots use 150ms. Nothing runs automatically
on USB connection. Turret steps are removed; new v2 demo mission IDs keep older ten-step runs separate.

### Real hardware on the prepared laptop

1. Use a working XIAO ESP32-S3 **Sense**, camera and antenna, with sound connections and no solder bridges.
   Keep the separate **motor battery disconnected** during upload; USB powers the XIAO.
2. Identify its new COM port with `warmo.ps1 -Action Status`. If absent, try a USB data cable, or hold BOOT while plugging in.
3. The prepared firmware already contains the saved temporary laptop-hotspot credentials in a private build copy.
   This starts that 2.4GHz WPA2 test network, validates all four flash-image hashes, uploads, and reads `WARMO IP:` at 115200:

   ```powershell
   .\warmo.ps1 -Action Flash -Port COM6 -BatteryDisconnected
   ```

   Replace `COM6` with the newly detected port. No motor commands are sent by this step.
4. Check the returned IP and camera without motion:

   ```powershell
   .\warmo.ps1 -Action CheckTank -XiaoUrl http://TANK_IP
   ```

5. Owner present, battery connected, tracks securely raised and fingers clear: run the initial bench test.
   Every action is announced at least 1.5 seconds before its pulse:

   ```powershell
   .\warmo.ps1 -Action RealDemo -XiaoUrl http://TANK_IP -TracksRaised -MissionId first-real-bench
   ```

6. After that bench test passes, put the tank on a clear floor for the drive demo:

   ```powershell
   .\warmo.ps1 -Action RealDemo -XiaoUrl http://TANK_IP -FloorClear -BenchPassed -MissionId first-real-floor
   ```

   These switches assert the owner's physical readiness; they are not automatic hardware checks.
   To select the continuous patrol on that checked hardware instead:

   ```powershell
   .\new -Real -XiaoUrl http://TANK_IP -FloorClear -BenchPassed
   # After Ctrl-C, resume the same selected real mission:
   .\go
   # Explicitly select simulation again:
   .\new -Sim
   ```

7. To let Claude Desktop control the checked real board, update **only WARMO's** connector and restart Claude:

   ```powershell
   .\warmo.ps1 -Action DesktopReal -XiaoUrl http://TANK_IP
   # Return to simulation later:
   .\warmo.ps1 -Action DesktopSim
   ```

The Desktop tools include `connection_status`, `look`, `demo_move`, `turret`, `stop`, and
`mission_start`, `mission_state`, `mission_record_patrol`, `advance`, `pivot`, and `get_sitrep`.
Other Claude MCP entries, including Wunder, are preserved. On this laptop the Desktop connector is now loaded in
**REAL** mode at `http://192.168.137.28`, with drive/pivot PWM 128 (about 50%). Its live camera and Terse memory
bridge passed read-only checks. The separate `new`/`go` patrol selection remains **SIM** until explicitly changed.
Turret commands are disabled in the patrol prompt, rules, Python MCP tool, v2 adapter, and operator helper.
The registered tool returns a disabled result without contacting the tank. Camera frames are rotated
**90 degrees counter-clockwise** (`CAM_ROTATE=270`) in the prepared Desktop and patrol paths.
The optional distance sensor may be absent; `/tof` then reports `-1` and no obstacle protection is available.

## Original autonomous brain (Linux / WSL2)

The original `brain.py` workflow below remains available. The prepared portable Linux runtime supplies Terse
for the Windows Desktop demo above; installing WSL is an alternative, not a prerequisite for that demo.

1. Admin PowerShell: `wsl --install` → reboot → make Ubuntu user.
2. In Ubuntu: `curl -LsSf https://astral.sh/uv/install.sh | sh` then reopen the terminal.
3. Copy this folder into WSL (e.g. `cp -r /mnt/c/Users/<you>/Downloads/warmo ~/warmo && cd ~/warmo`).
4. `cp .env.example .env` and set `ANTHROPIC_API_KEY`.
5. `uv sync`

## Run (3 terminals, all in ~/warmo)
```
# T1: durable mission memory (Terse)
uv run durable-actors dev            # wait for "Ready"
# T2: (first time) generate the client, then start the tank MCP server
uv run durable-actors generate
uv run tank_mcp.py                   # SIM=1 in .env = fake tank. Inspector: http://localhost:8000/
# T3: the brain
uv run brain.py --reset              # new mission
uv run brain.py                      # resume whatever mission is in memory
```
No API key / LLM acting up? `POLICY=rules uv run brain.py`

## Bring up the real tank (after the XIAO pins are soldered)
1. **Check for solder bridges first.** Never power it with pins fused.
2. Arduino IDE (Windows): open `firmware/warmo_firmware/warmo_firmware.ino`, create private `wifi_secrets.h` from its example (phone hotspot,
   Maximize Compatibility ON), board `XIAO_ESP32S3`, PSRAM `OPI PSRAM`, install library `Adafruit_VL53L1X`. Upload.
   Tank battery UNPLUGGED while flashing.
3. Serial Monitor 115200 → `WARMO IP: http://172.20.10.x`. Put it in `.env` as `XIAO_URL`, set `SIM=0`. Laptop on the same hotspot.
4. From WSL: `curl $XIAO_URL/` → `WARMO online`. `curl $XIAO_URL/capture -o t.jpg` → open the photo.
5. Tank propped up (tracks in the air), battery in:
   - `curl "$XIAO_URL/move?l=255&r=255&ms=300"` both tracks forward
   - `curl "$XIAO_URL/move?l=-255&r=255&ms=300"` pivot left
   If a motor doesn't move: that wire/pin isn't making contact.
6. Power the XIAO from the power bank, run T2 + T3.

## Calibration (5 min on the real floor)
- Pivot: `curl "$XIAO_URL/move?l=-255&r=255&ms=500"`, eyeball the degrees turned → `PIVOT_MS_PER_DEG = 500 / degrees`.
- Drive: `curl "$XIAO_URL/move?l=255&r=255&ms=1000"`, measure cm → `ADVANCE_MS_PER_CM = 1000 / cm`.
- Image sideways? `CAM_ROTATE=90` (or 270/180). Check `last.jpg` after a step.

## Safety (all in firmware, not the LLM)
- Every move requests a bounded duration (move ≤1s, ram ≤1.5s); the short demo requests ≤250ms drive pulses.
  Deadline checks occur between sensor reads and 10ms waits, so these are not hard real-time limits.
- ToF stops forward travel at 25cm when a working sensor is attached; absent ToF reports `-1`.
- Turret control is currently disabled through every prepared MCP/patrol/operator path. The board's older
  firmware endpoint is unchanged; do not use legacy direct `/turret` calls for this demo.
- Gun (D7) is never driven.
- PWM honors the requested duty (128/255 is about 50%). Motor channels and the camera use separate timers with an APB clock.
- HTTP requests are handled synchronously: `/stop` waits for a currently running bounded pulse to finish.
- Off switch = disconnect the separate motor battery.

## Demo script (2 min)
1. "This is Warmo. Waymo drives you home; Warmo patrols." Run `.\new`. It looks, narrates, turns, drives.
2. "Physical agents can't afford to forget mid-mission." **Ctrl-C the brain** mid-patrol → tank halts by itself.
3. Run `.\go` → `RESUMING mission`, saved sightings one per line, then continued patrol.
4. Optional: Ctrl-C the Terse server (`durable-actors dev`) too, restart it, the brain waits and reconnects with memory intact.
   (Use Ctrl-C, not kill -9: a hard kill holds a lock ~15s before it comes back.)
5. Show `uv run durable-actors inspect Mission/warmo-1` if they want to see the stored memory.

Original author-reported Linux checks: Terse restart recovery, hunt, and patrol. Current laptop evidence under
`C:\Users\Small\hw-test` separately records Manufact v2 tool tests, the scripted SIM demo, real HTTP-path tests
against an explicitly emulated firmware fixture, and actual Terse persistence checks.

Fresh laptop checks also ran the original `brain.py` in Linux with `SIM=1`, `POLICY=rules`: Ctrl-C after two
recorded steps, restart printed `RESUMING mission`, then continued to step four with resume count one.
Mission state survived both a Terse server restart and a complete portable-VM restart.
The hardened Windows `new` / `go` path also passed a SIM/rules patrol: Ctrl-C after 16 saved steps,
resume count one, then continued through step 28. The resume printed all 12 retained sightings on separate lines.
The fresh Linux brain rehearsal on October 31 saved three SIM/rules steps, received SIGINT, and restarted as
a new brain process on the same genuine Terse Mission. It printed all three saved sightings separately and
continued through step five with resume count one. The configured Desktop MCP entry independently read
that same SIM mission at step five. This fresh rehearsal did not rerun the Windows wrappers or drive the tank.
Claude Desktop supplies its own LLM when using the Desktop connector; the standalone patrol uses the configured provider.
One actual OpenRouter request also passed with requested/returned model `anthropic/claude-opus-5.5`:
it interpreted a simulated camera image, chose an advance, and recorded one completed step in genuine Terse.
There was no rules fallback or hardware command; the reported request cost was $0.00416.

Firmware **compiled on this laptop** for `XIAO_ESP32S3`, OPI PSRAM, core 3.3.12:
990,557 app bytes (29% of the selected partition). The prepared upload plan is
`C:\Users\Small\hw-test\build\WarmoReady\upload-plan.json`.
Replacement-board bench checks on October 31 passed: verified upload on COM7, real camera photos over HTTP
and Manufact v2 MCP at `http://192.168.137.28`, and owner-confirmed right/left track forward/backward plus turret
left/right and stops. Each motor test requested one 250ms pulse at about 50% PWM. The owner also reported
no movement when the separate motor battery was connected with the already-running board idle.
A fresh camera photo still worked after all motor pulses. The optional ToF was absent; its physical readings
remain untested and Mike chose to skip it for this demo. Resetting the XIAO with the motor battery connected was not tested.
Later floor tests confirmed forward/backward travel, but turret right failed after mounting/reconnection.
Mike subsequently disabled all turret control for the demo. Treat earlier turret bench results as historical.
Live supervised MCP track/pivot batches and camera observations also ran, with eight saved logical steps in
the genuine Terse mission `warmo-live-20261031`; movement distances/headings are requested estimates.

An intermittent HTTP stall occurred during these checks. Uptime continued and the vehicle later reported idle.
Closing the standalone browser camera tab was followed by five successful HTTP/camera requests; a held
browser connection is suspected, not proven. Keep extra camera tabs closed during the supervised patrol.
The supplied Claude Desktop completion capture subsequently reports a fresh-chat continuation of `warmo-3`
and 27 logged steps. That is a chat-reported live result; the independently recorded process-restart proof
above remains the SIM rehearsal. No new physical run was performed while preparing this README.
