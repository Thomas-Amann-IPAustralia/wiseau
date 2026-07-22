# Technical Specification — Universal Markdown Ingestion Engine

The detailed **"how"** to the project brief's **"why"**. Where the brief
([`project-brief.md`](project-brief.md)) sets vision and scope, this document is
the working contract: exact API shapes, module responsibilities, configuration,
error semantics, and the invariants every change must preserve.

If code and this spec disagree, that is a bug in one of them — reconcile them and
note it in [`decisions.md`](decisions.md).

---

## 1. Invariants (do not break these)

1. **Determinism.** For a fixed input, output Markdown is byte-identical across
   runs. No timestamps, no random ordering, no wall-clock-dependent content in
   the output. (Live web pages legitimately change; that is content drift, not a
   determinism violation — see §7.)
2. **Single contract.** Humans and agents receive the same `MarkdownResponse`
   JSON. There is no consumer-specific response shape.
3. **Normalization is universal.** Every Markdown-producing path ends in
   `cleaner.clean_markdown()`.
4. **Fair-use guards are unconditional.** Every heavy conversion runs inside the
   concurrency semaphore and under the rate limiter.
5. **Layer decoupling.** Frontend ↔ backend communicate only over HTTP; the
   frontend's only backend knowledge is `MARKDOWN_API_BASE`.

---

## 2. API contract

Base URL is the deployed backend (Hugging Face Space). All responses are JSON.
The machine-readable schema is FastAPI's auto-generated `/openapi.json`; the
interactive docs are at `/docs`.

### `GET /ping`
- **Purpose:** liveness/readiness for the UI status badge and background monitors.
- **Rate limit:** exempt.
- **200 response:**
  ```json
  { "status": "ok", "service": "markdown-ingestion-engine", "version": "0.1.0" }
  ```

### `POST /convert/url`
- **Purpose:** render a URL (JS-aware) and extract primary content as Markdown.
- **Rate limit:** `20/minute` per IP.
- **Request body:**
  ```json
  { "url": "https://example.com/article" }
  ```
  `url` is validated as an `HttpUrl`.
- **200 response:** `MarkdownResponse` (see §3).
- **Errors:** `422` invalid URL (FastAPI validation); `429` rate limited;
  `502` extraction/render failure.

### `POST /convert/file`
- **Purpose:** parse an uploaded PDF or DOCX into Markdown.
- **Rate limit:** `20/minute` per IP.
- **Request:** `multipart/form-data` with a single `file` field.
- **Constraints:** extension must be `.pdf` or `.docx`; body must be non-empty
  and ≤ `MAX_UPLOAD_BYTES` (default 25 MB).
- **200 response:** `MarkdownResponse`.
- **Errors:** `400` empty upload; `413` too large; `415` unsupported type;
  `429` rate limited; `502` parse failure.

---

## 3. Data model

`MarkdownResponse` (returned by both conversion endpoints):

| Field      | Type   | Meaning                                             |
| ---------- | ------ | --------------------------------------------------- |
| `source`   | string | The URL or original filename that was converted.    |
| `markdown` | string | The cleaned, normalized Markdown.                   |
| `length`   | int    | `len(markdown)` — a convenience for clients.        |

Keep this shape additive: new fields may be appended, but existing fields must
not change type or meaning without a version bump (§6).

---

## 4. Module responsibilities

The backend is deliberately small and layered. Each module has one job.

| Module | Responsibility | Must not |
| ------ | -------------- | -------- |
| `main.py` | HTTP surface: routing, validation, CORS, rate limiting, concurrency ceiling, upload limits, error → HTTP mapping. | Contain extraction logic. |
| `parsers/__init__.py` | Public entrypoints: `url_to_markdown`, `file_to_markdown`. | — |
| `parsers/browser.py` | Build a stealth headless Chrome driver. | Know about Markdown. |
| `parsers/url_parser.py` | Render → Trafilatura extract → (markdownify fallback) → clean. | Contain per-site CSS selectors. |
| `parsers/file_parser.py` | Dispatch by extension; PDF→PyMuPDF4LLM, DOCX→Mammoth; then clean. | Return unnormalized text. |
| `parsers/cleaner.py` | Deterministic Unicode/whitespace/typography normalization. | Introduce nondeterminism. |

### Extraction pipelines

**URL:** `initialize_driver()` renders the page (45s load timeout) → `page_source`
→ `trafilatura.extract(..., output_format="markdown", favor_precision=True)` →
if empty, `markdownify(html, heading_style="ATX")` → `clean_markdown()`.

**PDF:** `pymupdf.open(stream=...)` → `pymupdf4llm.to_markdown(doc)` → `clean_markdown()`.

**DOCX:** `mammoth.convert_to_html(...)` → `markdownify(..., heading_style="ATX")`
→ `clean_markdown()`.

### `clean_markdown()` guarantees
Given identical input it returns identical output: NFC Unicode normalization,
CRLF/CR → LF, smart-quotes/dashes/ellipsis/nbsp/zero-width/BOM → plain ASCII,
trailing whitespace stripped, runs of ≥3 blank lines collapsed to one blank
line, exactly one trailing newline.

---

## 5. Configuration

All backend configuration is via environment variables (12-factor).

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `PORT` | `7860` | Listen port (matches Hugging Face Spaces). |
| `MAX_CONCURRENT_JOBS` | `4` | Global concurrency ceiling for heavy jobs. |
| `MAX_UPLOAD_BYTES` | `26214400` | Upload size limit (25 MB). |
| `CHROME_BIN` | — | Path to Chromium binary (set in Docker image). |
| `CHROMEDRIVER_PATH` | — | Path to chromedriver (set in Docker image). |

Rate limits are code-level constants in `main.py` (`60/min` + `1000/day` default;
`20/min` on convert routes). Promote them to env vars only if a real tuning need
arises — record the change in `decisions.md`.

Frontend configuration is the single `window.MARKDOWN_API_BASE` in
`frontend/config.js`.

---

## 6. Versioning & change control

- The API `version` lives in `main.py` (`FastAPI(version=...)`) and is echoed by
  `/ping`. Bump it when the contract changes.
- **Additive** changes (new optional field, new endpoint) → patch/minor bump.
- **Breaking** changes (removed/renamed field, changed error semantics) → major
  bump **and** an ADR in `decisions.md` explaining why.
- Update this spec in the same commit as any contract change.

---

## 7. Error handling & edge cases

- **Render/extraction failure** (dead URL, timeout, driver crash) → `502` with a
  clean `detail` message; the exception is logged server-side, never leaked as a
  stack trace to the caller.
- **Unsupported file type** → `415`; **oversized** → `413`; **empty** → `400`.
- **Rate limit** → `429` (handled by `slowapi`); **concurrency overflow** →
  requests *queue* on the semaphore rather than erroring.
- **Content drift:** monitoring/diff pipelines must treat legitimate page changes
  as expected. Determinism is per-input, not across time.

---

## 8. Deployment topology

```
GitHub Pages (static frontend)  ──HTTPS──►  Hugging Face Space (Docker backend)
        index.html / app.js                    FastAPI + Chromium, 16 GB / 2 vCPU
```

- Backend image version-locks Chromium + Python via the `Dockerfile`; runs as
  non-root UID 1000 (Hugging Face requirement) on port 7860.
- Frontend is served as static files; the only per-deployment edit is
  `config.js` → `MARKDOWN_API_BASE` pointing at the Space URL.
- Cold-start is mitigated by the Space's long inactivity timeout, keeping the API
  warm enough for daily background checks.

---

## 9. Agent integration (Phase 4)

The agent-facing surface is documented in full in [`mcp.md`](mcp.md). Summary of
the contract-level guarantees:

- **MCP server** (`backend/mcp_server.py`) wraps `/convert/url`, `/convert/file`,
  and `/ping` as MCP tools (`convert_url`, `convert_file`, `ping`). It is a thin
  HTTP adapter over the running backend — every tool call is an HTTP request, so
  the tools reuse the exact `MarkdownResponse` shape and inherit the rate limiter
  and concurrency ceiling unchanged (invariant #4). No in-process bypass. Its
  only backend coupling is `WISEAU_API_BASE`, mirroring the frontend's
  `MARKDOWN_API_BASE` (ADR-009).
- **OpenAPI** is emitted at `/openapi.json` with explicit, clean operation IDs
  (`convert_url`, `convert_file`, `ping`) and per-route summaries so the schema
  reads well as a function-calling tool definition. Setting operation IDs is a
  fixed part of the contract now — do not let them regress to FastAPI's
  auto-generated `*_post` names.
- **Autonomous ingestion** (`backend/monitor.py`) consumes the same
  route (`POST /convert/url`) as a thin, stdlib-only HTTP client, so it inherits
  the same guards. It snapshots each URL's Markdown and diffs fresh conversions
  against the last, treating content drift as an expected `changed` outcome rather
  than an error (§7); only a failure to reach/render is an `error`. See
  [`mcp.md`](mcp.md) §3 and ADR-010.

See [`roadmap.md`](roadmap.md) for the task breakdown and [`mcp.md`](mcp.md) for
client wiring.
