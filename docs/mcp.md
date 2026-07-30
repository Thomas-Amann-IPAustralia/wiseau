# MCP & Function-Calling Integration

How external LLM agents consume the Markdown ingestion engine. This is the
Phase 4 (agent-native) integration surface. It does **not** introduce a new
contract — agents receive the same [`MarkdownResponse`](tech-spec.md#3-data-model)
(`source`, `markdown`, `length`) that the web UI does.

There are two ways to drive the engine from an agent:

1. **MCP tools** — `backend/mcp_server.py` exposes the engine as Model Context
   Protocol tools (Claude Desktop, IDE agents, custom MCP clients).
2. **OpenAI-style function calling** — the auto-generated
   [`/openapi.json`](tech-spec.md#2-api-contract) doubles as a function/tool
   schema; call the HTTP endpoints directly.

Both are thin views over the same three routes.

---

## 1. The MCP server

`backend/mcp_server.py` is a **thin HTTP adapter**, not a second engine. Each
tool call is an HTTP request to a running backend, so the MCP surface reuses the
exact `MarkdownResponse` contract and inherits the backend's fair-use guards —
per-IP rate limiting and the global concurrency ceiling — unchanged. There is no
in-process path that bypasses those guards (tech-spec invariant #4; see ADR-009).

```
agent  ──MCP (stdio/http)──►  mcp_server.py  ──HTTP──►  FastAPI backend
                                                        (rate limit + semaphore)
```

### Tools

| Tool | Arguments | Wraps | Returns |
| ---- | --------- | ----- | ------- |
| `convert_url` | `url: str` (absolute http/https), `engine: str = "auto"` | `POST /convert/url` | `{source, markdown, length}` |
| `convert_file` | `path: str` (local `.pdf`/`.docx`), `engine: str = "auto"` | `POST /convert/file` | `{source, markdown, length}` |
| `ping` | — | `GET /ping` | `{status, service, version, engines, default_engine}` |

`convert_file` reads the file from the machine running the MCP server (the usual
case: the server runs locally alongside the agent) and forwards its bytes and
filename to the backend, which dispatches parsers by extension.

`engine` is the same lever the web UI offers (ADR-025): `pymupdf` for the fast
deterministic parser, `docling` for the most faithful reading of a complex or
scanned document (much slower — tens of seconds to minutes on free CPU), or
`auto` to take the deployment's default, which is `pymupdf` unless the deployment
says otherwise (ADR-027). An unknown name comes back as the backend's 400.
Choosing `docling` does not disable the automatic fallback, so an agent that asks
for fidelity still gets a result when docling is down. `ping` reports the
accepted names in `engines` and the deployment's default in `default_engine` — an
agent that cares about latency should read it before sending `auto`.

Backend errors are surfaced verbatim: a failed tool raises with the backend's
`detail` message and status code (e.g. `wiseau backend error 415: Unsupported
file type '.txt'.`) rather than a stack trace.

### Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WISEAU_API_BASE` | `http://localhost:7860` | Base URL of the running backend. Mirrors the frontend's single `MARKDOWN_API_BASE` knob (ADR-004). |
| `WISEAU_MCP_TIMEOUT` | `120` | Per-request timeout (seconds); generous for slow renders. |
| `WISEAU_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` (see below); overridden by `--transport`. |
| `WISEAU_MCP_HOST` | `0.0.0.0` | Bind address, `streamable-http` only. |
| `WISEAU_MCP_PORT` | `8080` | Bind port, `streamable-http` only. |
| `WISEAU_MCP_ALLOWED_HOSTS` | — | Comma-separated hostnames to accept `Host` headers from, `streamable-http` only (ADR-028). |

### Two transports: local process vs. hosted connector

`mcp_server.py` supports both transports the `mcp` SDK offers. Pick the one that
matches where the calling agent runs:

- **`stdio` (default)** — the client spawns `mcp_server.py` itself as a
  subprocess and talks to it over stdin/stdout. This is the standard wiring for
  agents that live on the same machine as the server: Claude Desktop, Claude
  Code, most IDE agents.
- **`streamable-http`** — the server listens on a port and speaks MCP over HTTP,
  so **any** MCP client that can reach that URL can use it as a connector — not
  just ones that can spawn a local process. This is what makes wiseau usable from
  claude.ai custom connectors, other hosted agent frameworks, or any LLM tooling
  that only speaks HTTP (ADR-028).

### Running it

```bash
cd backend
pip install -r requirements.txt -r requirements-mcp.txt
# The backend must be running and reachable at WISEAU_API_BASE.

# stdio — local agent client spawns this process directly.
WISEAU_API_BASE=http://localhost:7860 python mcp_server.py

# streamable-http — serves the same tools over HTTP for remote/hosted clients.
WISEAU_API_BASE=https://your-space.hf.space \
WISEAU_MCP_ALLOWED_HOSTS=your-mcp-host.example.com \
    python mcp_server.py --transport streamable-http
```

`streamable-http` binds `WISEAU_MCP_HOST:WISEAU_MCP_PORT` (default
`0.0.0.0:8080`). The SDK rejects requests whose `Host` header isn't
`localhost`/`127.0.0.1` unless you list your real hostname in
`WISEAU_MCP_ALLOWED_HOSTS` — that guards against DNS rebinding, not against
unauthenticated access, so put the usual network boundary (reverse proxy,
platform access control) in front of it before exposing it publicly (ADR-028).

### Wiring into an MCP client

**Claude Desktop / Claude Code (stdio — spawns a local process):**

```json
{
  "mcpServers": {
    "wiseau": {
      "command": "python",
      "args": ["/absolute/path/to/wiseau/backend/mcp_server.py"],
      "env": { "WISEAU_API_BASE": "https://your-space.hf.space" }
    }
  }
}
```

Point `WISEAU_API_BASE` at your deployed Space (Phase 5) or a local backend.

**Remote/hosted clients (streamable-http — connects to a URL):**

Run the server with `--transport streamable-http` (above) somewhere reachable
from the client, then register it as a connector by URL —
`http://<host>:<port>/mcp` — the same way you'd add any other hosted MCP
connector. No local process for the client to spawn; any LLM/agent that speaks
MCP over HTTP can now call `convert_url`/`convert_file`/`ping`.

---

## 2. Function calling over the raw HTTP API

Agents that speak OpenAI-style function/tool calling don't need the MCP server —
`/openapi.json` already describes the endpoints with clean operation IDs
(`convert_url`, `convert_file`, `ping`) and summaries, so it can be handed to a
tool-calling loop directly. The agent then issues normal HTTP requests:

```bash
curl -X POST "$WISEAU_API_BASE/convert/url" \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com/article"}'
# -> {"source":"https://example.com/article","markdown":"# ...","length":1234}
```

Because both the MCP tools and direct callers hit the same routes, they observe
identical behaviour, identical output, and the same rate limits.

---

## 3. Autonomous ingestion (`backend/monitor.py`)

A worked example of an agent consuming the engine on a schedule:
`backend/monitor.py` watches one or more URLs, converting each to Markdown and
diffing every fresh conversion against the last one it saw. Like the MCP server it
is a **thin HTTP client** over `POST /convert/url` at `WISEAU_API_BASE`, so it
inherits the backend's rate-limit + concurrency guards unchanged (invariant #4).
It uses **only the Python standard library** — nothing to install beyond a
reachable backend.

It treats content drift as expected, not as a bug: determinism is **per input**,
not across time (tech-spec §7). Each check yields one of four outcomes —

| Status | Meaning |
| ------ | ------- |
| `new` | First time this URL is seen; the Markdown is saved as a baseline. |
| `unchanged` | Identical Markdown to the previous check. |
| `changed` | Content drift — a deterministic unified diff is attached. Still a success. |
| `error` | The backend was unreachable or the render failed; the last good baseline is left intact. |

Snapshots persist as one JSON file per URL under `WISEAU_SNAPSHOT_DIR` (default
`.wiseau-snapshots`), so state survives across runs.

```bash
cd backend
# Single pass — baseline on first sight, diff thereafter.
WISEAU_API_BASE=http://localhost:7860 \
    python monitor.py https://example.com/article

# Watch on a schedule (every hour) until interrupted.
python monitor.py --watch --interval 3600 https://example.com/article
```

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WISEAU_API_BASE` | `http://localhost:7860` | Backend base URL (shared with the MCP server). |
| `WISEAU_SNAPSHOT_DIR` | `.wiseau-snapshots` | Where per-URL snapshots are stored. |
| `WISEAU_MONITOR_TIMEOUT` | `120` | Per-request timeout (seconds). |

A single pass exits `0` unless a check errored (content changes are **not** an
error and stay exit `0`), so it composes cleanly into cron, a systemd timer, or a
CI job. The `--watch` loop is a self-contained scheduler for demos; production
scheduling is left to the operator. The reusable pieces (`fetch_markdown`,
`SnapshotStore`, `check_url`, `watch`) are importable for building a richer
integration. See [`roadmap.md`](roadmap.md) and ADR-010.
