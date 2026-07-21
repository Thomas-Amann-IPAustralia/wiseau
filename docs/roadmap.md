# Roadmap & Task Backlog

The execution tracker. Phases follow the [project brief](project-brief.md); this
file breaks them into checkable tasks and records progress. **Update the
checkboxes as you start and finish work, and mirror significant state changes in
[`STATUS.md`](../STATUS.md).**

Checkbox key: `[ ]` open · `[~]` in progress · `[x]` done & verified.

A task is only `[x]` when it has been *verified* (run, tested, or deployed) — not
merely written. Written-but-unverified is `[~]` with a note.

---

## Phase 1 — Backend API (Python engine)

- [~] Endpoints `GET /ping`, `POST /convert/url`, `POST /convert/file` — *code
  written in `main.py`, not yet run.*
- [~] Permissive CORS for public browser access — *written.*
- [~] Per-IP rate limiting via `slowapi` — *written.*
- [~] Global concurrency ceiling (semaphore) + upload size cap — *written.*
- [x] Boot the app and confirm all routes respond as specified. — *verified via
  FastAPI `TestClient`: `/ping`, `/convert/file` (real PDF), and `/convert/url`
  (browser worker mocked) all respond per contract; app imports cleanly with all
  parsers loaded.*
- [x] Pin dependency versions in `requirements.txt` (reproducibility). — *all
  direct deps pinned to verified versions; test tools in `requirements-dev.txt`.*

## Phase 2 — Algorithmic scraper integration

- [~] `initialize_driver()` with selenium-stealth + hardened Chrome args — *written.*
- [x] Trafilatura extraction with markdownify fallback — *exercised end-to-end by
  the opt-in live-browser test.*
- [x] Deterministic Markdown polish (`cleaner.py`) — *unit-tested (ADR-006) and
  re-confirmed through the live render path.*
- [x] Verify a live URL renders and extracts end-to-end. — *verified: with a
  version-matched Chromium + chromedriver, `url_to_markdown` launches headless
  Chrome, renders the DOM, and Trafilatura → cleaner emit clean Markdown. Codified
  as `tests/test_browser_live.py` (opt-in, `WISEAU_LIVE_BROWSER=1`) against a
  self-contained `data:` URL. Fetching arbitrary **external** URLs is blocked in
  this sandbox by its authenticated egress proxy — not a code issue; works with
  direct egress (Docker/Spaces). See ADR-007.*
- [x] Confirm determinism: same URL snapshot → identical Markdown across runs. —
  *`test_live_render_is_deterministic` asserts identical output across two runs.*

## Phase 3 — Static frontend (JS/CSS)

- [~] Responsive layout: URL tab, file drop zone, output pane — *written.*
- [~] `app.js` state management + `fetch` to backend — *written.*
- [~] Server-status badge pinging `/ping` on load — *written.*
- [x] Load the UI against a running backend and confirm URL + file conversion work. —
  *verified: drove the shipped UI with headless Chromium against a live `uvicorn`
  backend. Status badge → `online`; URL conversion (headless-Chrome render →
  Trafilatura → cleaner) against a locally-served article; PDF **and** DOCX upload
  conversion all render into the output pane. See ADR-008.*
- [x] Confirm copy/download of output and error states render sensibly. —
  *verified: Copy writes the markdown to the clipboard and flips to "Copied!";
  Download emits `converted.md`; a backend 415 renders the styled error state with
  the server's `detail`, and the empty-URL client guard shows its message. 18/18
  UI checks passed (ADR-008).*

## Phase 4 — AI & agentic integration

- [x] OpenAPI schema — *reviewed and refined: explicit clean operation IDs
  (`convert_url`, `convert_file`, `ping`) and per-route summaries set in
  `main.py`, so `/openapi.json` reads well as a function-calling tool definition.
  Verified via `app.openapi()`; API version bumped to `0.2.0` (ADR-009).*
- [x] MCP server wrapping `/convert/url` and `/convert/file` as tools (same contract). —
  *`backend/mcp_server.py` exposes `convert_url`, `convert_file`, and `ping` as
  MCP tools (FastMCP). Thin HTTP adapter over the backend, so the tools reuse the
  exact `MarkdownResponse` contract and inherit the rate-limit + concurrency
  guards (invariant #4). Verified end-to-end against a live `uvicorn` backend:
  `ping`, a real PDF through `convert_file`, and the 415 error path all confirmed;
  6 mocked-transport unit tests added (37 pass + 2 skipped total). See ADR-009.*
- [x] Document the function-calling / MCP integration for external agents. —
  *`docs/mcp.md`: tool table, config (`WISEAU_API_BASE`), how to run, Claude
  Desktop client wiring, and the OpenAPI/function-calling path. tech-spec §9
  rewritten to describe the built surface.*
- [ ] Autonomous ingestion example: scheduled diff-checking against saved snapshots.

## Phase 5 — Containerization & deployment

- [~] `Dockerfile` version-locking Chromium + Python — *written, not built/run in CI.*
- [~] Build the image and confirm Chromium launches inside the container. —
  *Chromium launch + full render→extract→clean pipeline proven on the host this
  session (ADR-007); building the actual Docker image and confirming launch
  inside the container is still open.*
- [ ] Deploy backend to a Hugging Face Space (free CPU tier).
- [ ] Point `frontend/config.js` `MARKDOWN_API_BASE` at the live Space.
- [ ] Deploy frontend via GitHub Pages.
- [ ] Confirm the deployed UI talks to the deployed backend end-to-end.

---

## Cross-cutting backlog (not phase-bound)

- [~] **Test suite.** Browser-free deterministic units (37 passing, 2 skipped):
  `cleaner` normalization/determinism, `file_parser` dispatch + real PDF **and
  DOCX** round-trips, request validation & error codes, `/convert/url` with a
  mocked driver, PDF/DOCX HTTP happy-paths, plus the **MCP tool surface**
  (`test_mcp_server.py`: contract round-trip, multipart forwarding, error-detail
  surfacing, tool registration — over a mocked HTTP transport). An opt-in
  live-browser test (`WISEAU_LIVE_BROWSER=1`) covers the real
  render→extract→clean pipeline. *Remaining:* Docker image build step in CI; live
  **external**-URL verification (needs direct egress).
- [~] **CI.** GitHub Actions (`.github/workflows/backend-tests.yml`) installs deps
  and runs `pytest` on `backend/**` changes. *Remaining:* Docker image build step.
- [x] **Dependency pinning** across `requirements.txt`.
- [ ] **Observability.** Structured request logging; a lightweight metric for
  job duration/memory to tune `MAX_CONCURRENT_JOBS` against real usage.
- [ ] **Abuse controls beyond rate limiting** (per-IP daily quota, optional API
  key tier) — only if fair-use limiting proves insufficient (see brief §7).

---

## How to use this file

1. Pick the next open (`[ ]`) task, preferring the "Suggested next actions" in
   `STATUS.md`.
2. Mark it `[~]` and note you're on it.
3. When done **and verified**, mark it `[x]`, update `STATUS.md`, and record any
   decision made along the way in `decisions.md`.
