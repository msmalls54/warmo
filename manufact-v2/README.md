# Manufact v2 connector

This is the actual **mcp-use 2.12.2** adapter used for WARMO's prepared Desktop and patrol paths, with portable
paths and gentle 128/255 drive defaults. It serves MCP through Manufact v2 and delegates tank behavior to
`../tank_mcp.py`, whose Python package is mcp-use 1.x. Mission tools call the genuine Terse runtime through
`../terse_memory.py`; they fail clearly when memory is unavailable.

## Fresh-clone SIM setup

Use Linux for Terse's runtime and Node.js 24 (the version used on the demo laptop). From the repository root:

```sh
uv sync
cp .env.example .env
npm --prefix manufact-v2 ci
```

Leave `SIM=1`. In separate terminals at the repository root:

```sh
# Terminal 1: durable memory
uv run durable-actors dev

# Terminal 2: generate the actor client, then expose the tank through v2
uv run durable-actors generate
SIM=1 WARMO_TRANSPORT=http node manufact-v2/desktop.mjs

# Terminal 3: no API key required for the rules rehearsal
SIM=1 MODE=patrol POLICY=rules MCP_URL=http://127.0.0.1:18080/mcp uv run brain.py --reset
# After a saved step, Ctrl-C. Resume with the same command WITHOUT --reset.
```

This portable packaging retains the tested adapter logic; its changed path defaults have been checked locally.
The complete fresh-clone installation has not been rerun for this publication. The prepared Windows helpers
still require their laptop-specific `~/hw-test` runtime; they do not install it from this repository.

## Claude Desktop

With the Terse runtime running and Python dependencies installed, add this local server to Desktop's MCP
configuration. Replace the absolute path for your checkout; use the existing configuration's other entries unchanged.

```json
{
  "mcpServers": {
    "WARMO": {
      "command": "node",
      "args": ["/absolute/path/to/warmo/manufact-v2/desktop.mjs"],
      "env": {
        "SIM": "1",
        "TERSE_BASE_URL": "http://127.0.0.1:7100",
        "CAM_ROTATE": "270",
        "DRIVE_SPEED": "128",
        "PIVOT_SPEED": "128"
      }
    }
  }
}
```

The adapter defaults to the checkout's `.venv` Python and `tank_mcp.py`. Set `WARMO_PYTHON`,
`WARMO_PYTHON_SERVER`, or `WARMO_TERSE_PYTHON` only when those executables/files live elsewhere.
For Windows Desktop, Terse must run separately on Linux; set `TERSE_BASE_URL` to its reachable control plane.
This repository does not provision that Linux runtime on Windows.

Start with `connection_status`, `mission_state`, and `look`. SIM is the default. Set `SIM=0` and `XIAO_URL`
only for checked hardware with its operator present. Use one controller at a time. Turret movement is a
disabled no-op; ram is not exposed. The optional ToF sensor can be absent, so obstacle protection may be unavailable.
