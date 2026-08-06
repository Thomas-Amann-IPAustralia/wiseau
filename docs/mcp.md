# MCP & Function-Calling Integration

How external LLM agents consume the Markdown ingestion engine. This is the
Phase 4 (agent-native) integration surface. It does **not** introduce a new
contract — agents receive the same [`MarkdownResponse`](tech-spec.md#3-data-model)
(`source`, `markdown`, `length`) that the web UI does.

There are two ways to drive the engine from an agent:

1. **MCP tools** — the engine's Model Context Protocol surface. A deployed
   backend serves it at `https://…/mcp`, so any LLM that accepts a connector URL
   can use it (ADR-029); the same tools also run as a local process for agents on
   your own machine. To deploy one, see [`hosting.md`](hosting.md).
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

In the **hosted** deployment those two boxes live in one process: the backend
serves the MCP endpoint itself at `/mcp`, and the tools reach the API over the
container's loopback interface (ADR-029). The arrow is still an HTTP request, so
the guards still apply — see §1.1.

### Tools

| Tool | Arguments | Wraps | Returns |
| ---- | --------- | ----- | ------- |
| `convert_url` | `url: str` (absolute http/https), `engine: str = "auto"`, `split_chapters: bool = False` | `POST /convert/url` | `{source, markdown, length}` (+ `chapters`, `chapter_detection`) |
| `convert_file` | `path: str` (local `.pdf`/`.docx`), `engine: str = "auto"`, `split_chapters: bool = False` | `POST /convert/file` | `{source, markdown, length}` (+ `chapters`, `chapter_detection`) |
| `convert_batch` | `paths: list[str]` (local `.pdf`/`.docx`), `engine: str = "auto"` | `POST /convert/batch` | `{count, succeeded, failed, results[]}` |
| `ping` | — | `GET /ping` | `{status, service, version, engines, default_engine, mcp_endpoint}` |

`convert_file` reads the file from the machine running the MCP server (the usual
case: the server runs locally alongside the agent) and forwards its bytes and
filename to the backend, which dispatches parsers by extension.

`convert_batch` is the tool for "convert this folder" / "convert all of these"
(ADR-031). Prefer it over a loop of `convert_file` calls: it is **one** request
against the backend's rate limit rather than one per document, and each result
arrives with the `filename` to save it as, already unique within the batch. Two
behaviours are worth knowing before writing the save loop:

* **One document failing does not fail the rest.** Each entry is
  `{status, source, filename, markdown, length, error}`; a failure has
  `status: "error"`, the message the single-document call would have returned,
  and **no `filename`**. So "write every result that has a filename" is the
  whole of a correct loop — an empty file can never land where a document
  should have been. Report the failures to the user; they are usually a wrong
  file type.
* **An unreadable path is refused up front**, before any conversion runs, so a
  typo in one path is reported rather than silently dropped from a batch that
  otherwise looks like a complete success.

The batch has no `split_chapters` — bulk conversion and chapter splitting are
mutually exclusive for now (ADR-031). To split a long document into per-chapter
files, call `convert_file` on it alone with `split_chapters=True`. Note also that
`engine="docling"` costs tens of seconds to minutes *per document*, so a batch of
any size wants the fast default.

`engine` is the same lever the web UI offers (ADR-025): `pymupdf` for the fast
deterministic parser, `docling` for the most faithful reading of a complex or
scanned document (much slower — tens of seconds to minutes on free CPU), or
`auto` to take the deployment's default, which is `pymupdf` unless the deployment
says otherwise (ADR-027). An unknown name comes back as the backend's 400.
Choosing `docling` does not disable the automatic fallback, so an agent that asks
for fidelity still gets a result when docling is down. `ping` reports the
accepted names in `engines` and the deployment's default in `default_engine` — an
agent that cares about latency should read it before sending `auto`.

`split_chapters` asks for the document *also* split into chapters (ADR-030) —
set it when the task is "break this book into files". Each chapter comes back as
`{title, level, filename, markdown, length}` with `filename` already numbered
(`03-the-reckoning.md`), so writing one file per chapter is a loop over
`chapters` and never a naming decision. `chapter_detection` says which signal
found them — `toc` (the document's own contents page, the most trustworthy),
`headings`, `markers`, `none`, or `error` (detection failed; the Markdown is
still good) — which is worth reporting to the user when the answer is one of the
weaker ones. A document with no chapter structure comes back
with `chapters: []` and `chapter_detection: "none"`; the full `markdown` is
always there either way. Leave it off for an ordinary page or a short document:
it roughly doubles the response for nothing.

Backend errors are surfaced verbatim: a failed tool raises with the backend's
`detail` message and status code (e.g. `wiseau backend error 415: Unsupported
file type '.txt'.`) rather than a stack trace.

### Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `WISEAU_API_BASE` | `http://localhost:7860` | Base URL of the running backend. Mirrors the frontend's single `MARKDOWN_API_BASE` knob (ADR-004). |
| `WISEAU_MCP_TIMEOUT` | `120` | Per-request timeout (seconds); generous for slow renders. |
| `WISEAU_MCP_MOUNT` | `1` | Whether the **backend** serves the MCP endpoint itself (ADR-029). `0` leaves only the REST API. |
| `WISEAU_MCP_PATH` | `/mcp` | Path of the hosted endpoint. An unguessable value makes the connector URL a shared secret — obscurity, not auth (ADR-029). |
| `WISEAU_MCP_ALLOWED_HOSTS` | — | Comma-separated hostnames whose `Host` header is accepted. **Unset (or `*`) disables the check**, which is what a public deployment needs; naming hosts enforces them (ADR-029). |
| `WISEAU_MCP_TRANSPORT` | `stdio` | Standalone process only: `stdio` or `streamable-http`; overridden by `--transport`. |
| `WISEAU_MCP_HOST` | `0.0.0.0` | Bind address, standalone `streamable-http` only. |
| `WISEAU_MCP_PORT` | `8080` | Bind port, standalone `streamable-http` only. |

### 1.1 Hosted: the backend serves the endpoint (the deployment path)

The deployed backend answers MCP over HTTP at `/mcp` on its own port and
hostname, so **the service URL is the connector URL** — `https://…/mcp` — and
there is no second process to deploy (ADR-029). This is what makes wiseau usable
by an LLM that isn't running on your machine, and it is what
[`hosting.md`](hosting.md) walks through click by click.

`GET /ping` reports the path as `mcp_endpoint`, so a client discovers it rather
than assuming `/mcp` (`WISEAU_MCP_PATH` may have moved it).

Three properties are worth knowing:

- **The guards still apply.** The tools call `/convert/*` over the container's
  loopback interface, not through an in-process shortcut, so rate limiting and
  the concurrency ceiling hold (invariant #4). The cost is that every MCP caller
  shares the loopback address's rate-limit bucket: `20/minute` in total across
  all MCP traffic, not per agent.
- **Sessions are stateless.** A scale-to-zero host may answer two requests of one
  conversation from two instances.
- **It is unauthenticated.** Anyone with the URL can convert on your quota; see
  `hosting.md` §8 for what actually bounds that.

### 1.2 Standalone: a separate process (local agents)

`mcp_server.py` also runs on its own, which is the right answer when the agent
lives on the same machine as the server:

- **`stdio` (default)** — the client spawns `mcp_server.py` as a subprocess and
  talks over stdin/stdout. Claude Desktop, Claude Code, most IDE agents.
- **`streamable-http`** — this module binds its own port and speaks MCP over
  HTTP (ADR-028). Since ADR-029 the hosted endpoint above covers this case with
  one fewer service, so reach for it only when you want the MCP surface on a
  different host or port from the API.

```bash
cd backend
pip install -r requirements.txt -r requirements-mcp.txt
# The backend must be running and reachable at WISEAU_API_BASE.

# stdio — local agent client spawns this process directly.
WISEAU_API_BASE=http://localhost:7860 python mcp_server.py

# streamable-http — a second, separate HTTP surface on WISEAU_MCP_HOST:PORT.
WISEAU_API_BASE=https://your-service.example.com \
    python mcp_server.py --transport streamable-http
```

### Wiring into an MCP client

**Any remote/hosted client (the deployed service):** register
`https://<your deployment>/mcp` as a custom connector / remote MCP server. That
is the whole configuration — claude.ai, Claude Desktop, ChatGPT, and agent
frameworks all take a URL. Per-client clicks are in
[`hosting.md` §9](hosting.md).

```bash
# Claude Code, for example:
claude mcp add --transport http wiseau https://<your deployment>/mcp
```

**Claude Desktop / Claude Code against a *local* server (stdio — spawns a
process):**

```json
{
  "mcpServers": {
    "wiseau": {
      "command": "python",
      "args": ["/absolute/path/to/wiseau/backend/mcp_server.py"],
      "env": { "WISEAU_API_BASE": "http://localhost:7860" }
    }
  }
}
```

Point `WISEAU_API_BASE` at a local backend or at your deployment.

---

## 2. Function calling over the raw HTTP API

Agents that speak OpenAI-style function/tool calling don't need the MCP server —
`/openapi.json` already describes the endpoints with clean operation IDs
(`convert_url`, `convert_file`, `convert_batch`, `ping`) and summaries, so it can
be handed to a tool-calling loop directly. The agent then issues normal HTTP
requests:

```bash
curl -X POST "$WISEAU_API_BASE/convert/url" \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com/article"}'
# -> {"source":"https://example.com/article","markdown":"# ...","length":1234}

# A long, chaptered document, split into per-chapter files:
curl -X POST "$WISEAU_API_BASE/convert/file" \
     -F 'file=@book.pdf' -F 'split_chapters=true'
# -> {..., "chapter_detection":"toc",
#     "chapters":[{"title":"Chapter 1: The Arrival","level":2,
#                  "filename":"01-chapter-1-the-arrival.md","markdown":"...","length":3759}, ...]}

# Several documents at once — one request, one Markdown file each:
curl -X POST "$WISEAU_API_BASE/convert/batch" \
     -F 'files=@annual-report.pdf' -F 'files=@minutes.docx' -F 'files=@notes.txt'
# -> {"count":3,"succeeded":2,"failed":1,
#     "results":[{"status":"ok","source":"annual-report.pdf",
#                 "filename":"annual-report.md","markdown":"# ...","length":8214},
#                {"status":"ok","source":"minutes.docx","filename":"minutes.md", ...},
#                {"status":"error","source":"notes.txt","filename":null,
#                 "error":"Unsupported file type '.txt'. ..."}]}
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
