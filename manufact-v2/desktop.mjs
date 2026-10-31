// Claude Desktop stdio entry. Manufact v2 handles every MCP request in-process;
// the existing Python server remains the owner of tank behaviour and vision.
import { mkdir } from "node:fs/promises";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { z } from "zod";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { timingSafeEqual } from "node:crypto";

const execFileAsync = promisify(execFile);

// stdout is reserved for Claude's JSON-RPC transport.
console.log = (...args) => console.error(...args);
process.env.MCP_USE_ANONYMIZED_TELEMETRY = "false";
const { MCPServer } = await import("mcp-use");

const runtime = resolve(import.meta.dirname, "..");
const python = process.env.WARMO_PYTHON ?? resolve(runtime, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const source = process.env.WARMO_PYTHON_SERVER ?? resolve(runtime, "tank_mcp.py");
const data = process.env.WARMO_DATA_DIR ?? resolve(runtime, ".warmo-data");
const configuredMode = process.env.SIM ?? "1";
if (configuredMode !== "0" && configuredMode !== "1") throw new Error("SIM must be exactly 1 (simulation) or 0 (real hardware)");
const simulation = configuredMode === "1";
const configuredPivotInvert = process.env.PIVOT_INVERT ?? "0";
if (configuredPivotInvert !== "0" && configuredPivotInvert !== "1") throw new Error("PIVOT_INVERT must be exactly 0 or 1");
const pivotInvertEffective = !simulation && configuredPivotInvert === "1";
const steeringDirectionContract = "Tool left/right arguments describe physical directions. Use the current steering calibration; do not compensate again in prompts or goal notes.";
const steeringCalibration = { pivot_invert_configured: configuredPivotInvert === "1", pivot_invert_effective: pivotInvertEffective, direction_contract: steeringDirectionContract };
const steeringInstructions = `${pivotInvertEffective ? "STEERING CALIBRATION: PIVOT_INVERT=1 is active for REAL mode. The hardware boundary already corrects pivot direction once. Ignore obsolete mission notes that say left/right turns are inverted; do not flip commands again. " : ""}${steeringDirectionContract}`;
const cameraRotation = Number(process.env.CAM_ROTATE ?? "270");
const driveSpeed = Number(process.env.DRIVE_SPEED ?? "128");
const pivotSpeed = Number(process.env.PIVOT_SPEED ?? "128");
if (![0, 90, 180, 270].includes(cameraRotation)) throw new Error("CAM_ROTATE must be 0, 90, 180, or 270");
if (![driveSpeed, pivotSpeed].every((value) => Number.isInteger(value) && value >= 0 && value <= 255)) throw new Error("Motor speeds must be whole numbers from 0 to 255");
const servingTransport = process.env.WARMO_TRANSPORT ?? "stdio";
if (!["stdio", "http"].includes(servingTransport)) throw new Error("WARMO_TRANSPORT must be stdio or http");
await mkdir(data, { recursive: true });

const upstream = new Client({ name: "warmo-patrol-manufact-v2", version: "1.0.0" });
const transport = new StdioClientTransport({
  command: python,
  args: ["-u", source],
  cwd: data,
  env: {
    ...process.env,
    SIM: simulation ? "1" : "0",
    MCP_TRANSPORT: "stdio",
    WARMO_DATA_DIR: data,
    MCP_USE_ANONYMIZED_TELEMETRY: "false",
    PYTHONUNBUFFERED: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    PIVOT_INVERT: configuredPivotInvert,
    CAM_ROTATE: String(cameraRotation), DRIVE_SPEED: String(driveSpeed), PIVOT_SPEED: String(pivotSpeed),
  },
  stderr: "pipe",
});
transport.stderr?.on("data", (chunk) => process.stderr.write(chunk));
upstream.onerror = (error) => console.error("WARMO upstream:", error.message);

let server;
let closing;
async function close() {
  if (!closing) closing = Promise.allSettled([server?.close(), upstream.close()]);
  await closing;
}
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, async () => {
    await close();
    process.exit(0);
  });
}

try {
  await upstream.connect(transport);
  const listed = await upstream.listTools();
  const expected = ["advance", "connection_status", "demo_move", "get_sitrep", "look", "pivot", "sim_reset", "stop", "turret"];
  if (JSON.stringify(listed.tools.map((tool) => tool.name).sort()) !== JSON.stringify(expected)) {
    throw new Error("Unexpected WARMO tools; refusing to expose a different server");
  }

  server = new MCPServer({
    name: "warmo-patrol-manufact-v2",
    version: "1.0.0",
    description: "WARMO camera scout and patrol tools using Manufact mcp-use 2.12.2",
    instructions: `${simulation ? "SIMULATION MODE: tools control a patrol simulation, not the physical vehicle." : "REAL TANK MODE: tools control the physical patrol vehicle."} ${upstream.getInstructions() ?? "Scout the surroundings, make one short move, and observe again. Every move self-stops."} ${steeringInstructions}`,
    legacy: "stateless",
    logging: { enabled: false },
    ...(servingTransport === "http" ? { allowedHosts: ["10.0.2.2"], allowedOrigins: ["10.0.2.2"] } : {}),
  });
  for (const tool of listed.tools) {
    const disabledTurret = tool.name === "turret";
    server.tool(
      {
        name: tool.name,
        description: disabledTurret ? "DISABLED: turret movement is unavailable. Returns a no-op without sending hardware commands." : tool.name === "pivot" ? `${tool.description ?? "Pivot left or right."} ${steeringInstructions}` : tool.description,
        inputSchema: z.fromJSONSchema(tool.inputSchema),
        annotations: disabledTurret ? { readOnlyHint: true, destructiveHint: false } : tool.annotations,
      },
      async (args) => disabledTurret ? { content: [{ type: "text", text: JSON.stringify({ ok: true, disabled: true, moved: false, ran_ms: 0, reason: "Turret movement is disabled; no hardware command was sent." }) }] } : upstream.callTool({ name: tool.name, arguments: args }),
    );
  }
  const missionId = z.string().min(1).max(100).regex(/^[a-zA-Z0-9_.-]+$/);
  const memoryPython = process.env.WARMO_TERSE_PYTHON ?? python;
  const memoryHelper = process.env.WARMO_TERSE_HELPER ?? resolve(source, "../terse_memory.py");
  async function memory(action, id, payload = {}) {
    let result;
    try {
      const { stdout } = await execFileAsync(memoryPython, [memoryHelper, action, id, JSON.stringify(payload)], {
        cwd: data,
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
        timeout: 55000,
        maxBuffer: 1024 * 1024,
        windowsHide: true,
      });
      result = JSON.parse(stdout);
    } catch (error) {
      try { result = JSON.parse(error.stdout ?? ""); }
      catch { result = { ok: false, error: "Terse mission memory is unavailable. Start the Linux Terse runtime and check TERSE_BASE_URL." }; }
    }
    return { content: [{ type: "text", text: JSON.stringify(result) }], ...(result.ok === true ? {} : { isError: true }) };
  }
  server.tool({
    name: "mission_start",
    description: "Start or resume durable WARMO mission memory in the real Terse runtime. Fails if Terse is unavailable; never substitutes local JSON memory.",
    inputSchema: z.object({ mission_id: missionId, goal: z.string().min(1).max(500) }),
  }, ({ mission_id, goal }) => memory("start", mission_id, { goal }));
  server.tool({
    name: "mission_state",
    description: "Read the mission's durable Terse state, previous actions, sightings, and resume count.",
    inputSchema: z.object({ mission_id: missionId }),
    annotations: { readOnlyHint: true },
  }, ({ mission_id }) => memory("state", mission_id));
  server.tool({
    name: "mission_record_patrol",
    description: "Save one completed observation or movement to the mission's real Terse durable actor.",
    inputSchema: z.object({
      mission_id: missionId, action: z.string().min(1).max(500), saw: z.string().max(1000).default(""),
      turned_deg: z.number().finite().default(0), moved_cm: z.number().finite().default(0),
    }),
  }, ({ mission_id, ...payload }) => memory("record_patrol", mission_id, payload));
  if (servingTransport === "http") {
    const requestedPort = Number(process.env.WARMO_HTTP_PORT ?? "18080");
    if (!Number.isInteger(requestedPort) || requestedPort < 0 || requestedPort > 65535) throw new Error("WARMO_HTTP_PORT must be an integer from 0 to 65535");
    const shutdownToken = process.env.WARMO_HTTP_SHUTDOWN_TOKEN;
    if (shutdownToken !== undefined && !/^[0-9a-f]{64}$/i.test(shutdownToken)) throw new Error("WARMO_HTTP_SHUTDOWN_TOKEN must be 64 hexadecimal characters");
    server.app.use("*", async (context, next) => {
      const authority = context.req.header("host") ?? new URL(context.req.url).host;
      const hostname = authority.toLowerCase().replace(/:\d+$/, "");
      if (!["127.0.0.1", "localhost", "10.0.2.2"].includes(hostname)) return context.json({ error: "invalid host" }, 403);
      await next();
    });
    server.get("/health", (context) => context.json({ name: "warmo-patrol-manufact-v2", mode: simulation ? "SIM" : "REAL", xiao_url: simulation ? null : (process.env.XIAO_URL ?? null), pid: process.pid, transport: "http", ready: true, tools: listed.tools.length + 3, camera_rotation: cameraRotation, drive_speed: driveSpeed, pivot_speed: pivotSpeed, pivot_invert: Number(configuredPivotInvert), pivot_invert_effective: pivotInvertEffective, steering_calibration: steeringCalibration, turret_disabled: true, version: "2.12.2" }));
    server.post("/_warmo/shutdown", (context) => {
      if (shutdownToken === undefined) return context.json({ error: "not enabled" }, 404);
      const supplied = context.req.header("X-Warmo-Shutdown-Token") ?? "";
      if (Buffer.byteLength(supplied) !== Buffer.byteLength(shutdownToken) || !timingSafeEqual(Buffer.from(supplied), Buffer.from(shutdownToken))) return context.json({ error: "unauthorized" }, 403);
      setTimeout(async () => { await close(); process.exit(0); }, 50);
      return context.json({ ok: true });
    });
    const listening = await server.listen(requestedPort, { host: "127.0.0.1" });
    console.error(`WARMO patrol HTTP ready | Manufact mcp-use 2.12.2 | ${simulation ? "SIM" : "REAL"} | ${listed.tools.length + 3} tools | http://127.0.0.1:${listening.port}/mcp`);
    await new Promise(() => {});
  } else {
  console.error(`WARMO patrol ready | Manufact mcp-use 2.12.2 | ${simulation ? "SIM" : "REAL"} | ${listed.tools.length + 3} tools | no network listener`);

  // v2's documented serving boundary is fetch(). Adapt Desktop's line-delimited
  // transport directly to that boundary; no port, tunnel, or cloud is required.
  let protocol = "2025-11-25";
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
      if (message.method === "initialize") protocol = message.params?.protocolVersion ?? protocol;
      const response = await server.fetch(new Request("http://localhost/mcp", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json, text/event-stream",
          "mcp-protocol-version": protocol,
        },
        body: line,
      }));
      if (response.status === 204 || response.status === 202) continue;
      const body = await response.text();
      const replies = response.headers.get("content-type")?.includes("text/event-stream")
        ? body.split(/\r?\n/).filter((part) => part.startsWith("data:")).map((part) => part.slice(5).trim()).filter(Boolean).map(JSON.parse)
        : body.trim() ? [JSON.parse(body)] : [];
      for (const reply of replies) process.stdout.write(JSON.stringify(reply) + "\n");
    } catch (error) {
      console.error("WARMO Desktop request failed:", error.message);
      if (message?.id !== undefined) {
        process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32603, message: error.message } }) + "\n");
      }
    }
  }
  }
} finally {
  await close();
}
