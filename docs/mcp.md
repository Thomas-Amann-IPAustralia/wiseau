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
| `convert_url` | `url: str` (absolute http/https) | `POST /convert/url` | `{source, markdown, length}` |
| `convert_file` | `path: str` (local `.pdf`/`.docx`) | `POST /convert/file` | `{source, markdown, length}` |
| `ping` | — | `GET /ping` | `{status, service, version}` |

`convert_file` reads the file from the machine running the MCP server (the usual
case: the server runs locally alongside the agent) and forwards its bytes and
filename to the backend, which dispatches parsers by extension.

Backend errors are surfaced verbatim: a failed tool raises with the backend's
`detail` message and status code (e.g. `wiseau backend error 415: Unsupported
file type '.txt'.`) rather than a stack trace.

### Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WISEAU_API_BASE` | `http://localhost:7860` | Base URL of the running backend. Mirrors the frontend's single `MARKDOWN_API_BASE` knob (ADR-004). |
| `WISEAU_MCP_TIMEOUT` | `120` | Per-request timeout (seconds); generous for slow renders. |

### Running it

```bash
cd backend
pip install -r requirements.txt -r requirements-mcp.txt
# The backend must be running and reachable at WISEAU_API_BASE.
WISEAU_API_BASE=http://localhost:7860 python mcp_server.py
```

This starts the server on the default **stdio** transport — the standard wiring
for local agent clients. For a remote deployment, `mcp.run("streamable-http")`
serves the same tools over HTTP.

### Wiring into an MCP client (Claude Desktop example)

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

## 3. Autonomous ingestion (roadmap)

The remaining Phase 4 item — scheduled diff-checking against saved snapshots —
consumes exactly these tools/endpoints. It must treat legitimate page changes as
expected: determinism is **per input**, not across time (tech-spec §7). A monitor
stores the last `markdown` for a URL, re-runs `convert_url` on a schedule, and
diffs the two strings. Not yet built; see [`roadmap.md`](roadmap.md).
