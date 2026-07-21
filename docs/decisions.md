# Architecture Decision Log (ADRs)

A running record of **non-trivial decisions and their rationale**, so a later
Claude Code instance doesn't relitigate a settled choice — or, if it should be
revisited, can see exactly what was weighed.

Add a new entry at the top when you make a decision that future work would
otherwise have to reverse-engineer. Keep entries short. When a decision is
overturned, don't delete it — add a new entry that supersedes it and mark the old
one `Superseded`.

**Entry template:**

```
## ADR-NNN — <short title>
**Date:** YYYY-MM-DD · **Status:** Accepted | Superseded by ADR-XXX | Proposed
**Context:** what forced a choice.
**Decision:** what we chose.
**Consequences:** what this makes easy/hard; what to watch for.
```

---

## ADR-000 — Establish the documentation foundation for an instance chain
**Date:** 2026-07-20 · **Status:** Accepted
**Context:** The project is executed by a series of Claude Code instances with no
shared memory beyond the repo. Without durable, role-separated docs, each
instance re-derives context and drifts off-spec.
**Decision:** Maintain six documents with distinct roles — `CLAUDE.md` (entry
point/rules), `STATUS.md` (live state), `project-brief.md` (vision),
`tech-spec.md` (contract), `roadmap.md` (task backlog), `decisions.md` (this
log) — plus `agent-workflow.md` (handoff protocol). Facts-vs-behaviour-vs-
rationale-vs-todo are kept in separate files to avoid overlap and staleness.
**Consequences:** Each instance has one predictable place to look and to update.
Cost: the docs must be kept current — enforced by the "update STATUS.md before
you finish" rule in `CLAUDE.md` and `agent-workflow.md`.

## ADR-001 — Deterministic algorithmic extraction over per-site selectors
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Content extraction can be done with hand-tuned CSS selectors per
site or algorithmically. Selectors are precise but brittle and non-portable.
**Decision:** Use Trafilatura for main-content extraction, with markdownify only
as a fallback when Trafilatura returns nothing. No per-site selectors.
**Consequences:** Robust across arbitrary layouts and the core of the determinism
guarantee. Some sites may extract imperfectly; that is accepted over brittleness.
Adding per-site logic would violate this ADR — open a new one if ever needed.

## ADR-002 — Open, public API guarded by fair-use limits, not origin locks
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** The API serves the hosted UI, forks, third-party frontends, and
agents. Locking CORS to one origin would block legitimate reuse.
**Decision:** Permissive CORS (`allow_origins=["*"]`, no credentials). Protect the
shared compute with per-IP rate limiting (`slowapi`) and a global concurrency
ceiling (`asyncio.Semaphore`) rather than access control.
**Consequences:** Anyone can call it ("pay it forward"). Abuse risk is real;
first defence is rate limiting, with quotas/API-key tiers held in reserve (brief
§7). Every heavy endpoint must stay behind both guards.

## ADR-003 — Single response contract for humans and agents
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Two consumer classes (UI, LLM/agents) could each get a tailored
response shape.
**Decision:** One `MarkdownResponse` (`source`, `markdown`, `length`) for both,
described by the auto-generated OpenAPI schema.
**Consequences:** One thing to maintain and version; MCP/agent integration reuses
it directly. Consumer-specific needs must be met additively, not by forking the
contract.

## ADR-004 — Decoupled static frontend + containerized backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** UI and engine have very different hosting and iteration needs.
**Decision:** Static frontend on GitHub Pages; containerized FastAPI backend on
Hugging Face Spaces; they communicate only over HTTP. The frontend's sole backend
coupling is `MARKDOWN_API_BASE` in `config.js`.
**Consequences:** Each layer deploys and evolves independently; free hosting on
both tiers. No build-time coupling is permitted between them.

## ADR-005 — Version-locked Docker image for the backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Headless Chromium extraction is sensitive to system-library and
browser versions; "works on my machine" drift would break determinism.
**Decision:** Ship a `Dockerfile` (Python 3.11-slim + system Chromium/chromedriver)
that version-locks the runtime; run as non-root UID 1000 for Hugging Face.
**Consequences:** Reproducible runtime. *Follow-up (resolved 2026-07-20):* Python
dependencies in `requirements.txt` are now version-pinned — see ADR-006 —
completing this decision's intent.

## ADR-009 — MCP server as a thin HTTP adapter over the backend
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** Phase 4 calls for an MCP server wrapping `/convert/url` and
`/convert/file` as agent tools. Two implementations were possible: (a) import the
`parsers` functions in-process and run extraction inside the MCP process, or (b)
make the MCP server an HTTP client of the running backend. Option (a) is one
fewer moving part but bypasses the rate limiter and concurrency ceiling, which
tech-spec invariant #4 says are *unconditional* for every heavy conversion, and
would create a second place the extraction stack is wired up (drift risk).
**Decision:** Build `backend/mcp_server.py` as a **thin HTTP adapter** (option b)
using the MCP SDK's `FastMCP`. Each tool (`convert_url`, `convert_file`, `ping`)
issues an HTTP request to the backend at `WISEAU_API_BASE` (default
`http://localhost:7860`), so it reuses the exact `MarkdownResponse` contract and
inherits the fair-use guards unchanged — mirroring the frontend's single
`MARKDOWN_API_BASE` coupling (ADR-004). Backend error `detail` is surfaced
verbatim. `convert_file` reads a local path (the MCP server runs alongside the
agent) and forwards bytes + filename so the backend still dispatches by
extension. Also refined the OpenAPI surface: explicit operation IDs
(`convert_url`/`convert_file`/`ping`) + per-route summaries so `/openapi.json`
reads cleanly as a function-calling schema; bumped the API version to `0.2.0`.
The MCP SDK is an optional extra (`requirements-mcp.txt`), added to
`requirements-dev.txt` so CI can test the adapter; `httpx` was already a dev dep.
**Consequences:** One extraction path, one contract, guards always in force. The
tools require a reachable backend (documented) — acceptable, and consistent with
the decoupled architecture. Tested with a mocked `httpx` transport (no live
server/browser) and verified end-to-end against a live `uvicorn` backend
(`ping`, real-PDF `convert_file`, 415 error path). Remaining Phase 4 item:
autonomous ingestion (scheduled diff-checking). See `docs/mcp.md`.

## ADR-007 — Live browser path verified; real DOCX round-trip added; live test kept opt-in
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** After ADR-006, the two biggest automation gaps were the live
Chromium render path (the project's biggest unknown) and a real DOCX-body
round-trip. The live path was verified end-to-end this session: with a
version-matched Chromium + chromedriver, `url_to_markdown` launches headless
Chrome, renders the DOM, and Trafilatura → cleaner produce clean Markdown.
Two practical findings: (1) chromedriver must match the Chrome *major* version —
a mismatched driver on `PATH` fails to start a session; Selenium Manager
(`selenium-manager --browser chrome --browser-version <N>`) auto-fetches the
matching pair, and `browser.py` already honours `CHROME_BIN`/`CHROMEDRIVER_PATH`
for explicit pinning. (2) This CI/sandbox forces outbound HTTPS through an
authenticated proxy that headless Chrome cannot consume via `--proxy-server`, so
fetching *arbitrary external* URLs is blocked *here* — not a code defect; the
Docker/Spaces deployment has direct egress.
**Decision:** Keep the live-browser test **opt-in** — a new
`tests/test_browser_live.py`, skipped unless `WISEAU_LIVE_BROWSER=1`, exercising
the full render → extract → clean pipeline against a self-contained `data:` URL
(no network) so it is repeatable without egress. Default CI stays browser-free.
Add real DOCX round-trip tests (dispatch, structure, determinism, and an HTTP
happy-path) using in-memory `python-docx` fixtures; pin `python-docx` in
`requirements-dev.txt`.
**Consequences:** Phase 2's render path is proven and codified as a runnable test
rather than a one-off manual check; the DOCX-body path is now covered by
automation. CI remains fast and browserless. To run the live test in a new
environment, ensure a matching Chrome/chromedriver (Selenium Manager handles this
by default) and set `WISEAU_LIVE_BROWSER=1`. Verifying live *external* URLs still
requires an environment with direct egress (e.g. the Docker image, Phase 5).

## ADR-006 — Browser-free deterministic test suite + pinned dependencies
**Date:** 2026-07-20 · **Status:** Accepted
**Context:** The engine had no tests and no CI, and its dependencies were
unpinned — both flagged risks to the determinism guarantee. The heavy path (URL
rendering) needs Chromium, which is slow and awkward to provision in CI, but the
load-bearing determinism logic (`cleaner`, file dispatch, request validation)
does not.
**Decision:** Test the browser-free half directly and mock the Chromium worker
(`url_to_markdown`) so the `/convert/url` route's plumbing is still covered
without a browser. PDF paths use in-memory PyMuPDF-generated documents, so no
fixture files are needed. Pin every direct dependency in `requirements.txt` to
the versions the suite was verified against; keep test-only tools in
`requirements-dev.txt`. CI (`.github/workflows/backend-tests.yml`) installs both
and runs `pytest` on any `backend/**` change — no browser provisioned.
**Consequences:** Fast, deterministic CI that guards the contract on every push.
The live browser-render and DOCX-body paths remain unproven by automation and
still need manual/Docker verification (tracked in `roadmap.md`). Dependency bumps
are now deliberate: change the pin, re-run the suite.

## ADR-008 — Frontend (Phase 3) verified by driving the real UI against a live backend
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** The static frontend (`index.html`/`app.js`/`config.js`) was
code-complete but had never been exercised against a running backend, so Phase 3's
two verification tasks were still open. The decoupling rule (frontend ↔ backend
only over HTTP via `MARKDOWN_API_BASE`) means the only honest way to mark them
`[x]` is to actually load the page in a browser and watch it talk to the API.
**Decision:** Verify by driving the shipped UI end-to-end with headless Chromium
(Playwright) against a locally-running stack — `uvicorn` backend on `:7860`, the
static frontend served on `:8000`, and a throwaway fixtures server on `:8001` so
the URL path renders a *local* article page (no external egress needed here). The
driver script asserts the observable contract: status badge → `online`, URL
conversion (headless-Chrome render → Trafilatura → cleaner → output pane with a
char-count meta), PDF **and** DOCX upload conversion, copy (clipboard content
checked) and download (`converted.md`), and error rendering for a backend 415 plus
the client-side empty-URL guard. 18/18 checks passed. The driver is a
verification artifact, not committed app code — the frontend has no build step and
we keep it framework-free (ADR-004).
**Consequences:** Phase 3 is proven, not just written; the shared response
contract is confirmed to render correctly for both success and error paths in a
real browser. The check is reproducible in any environment with a browser but is
not wired into CI (the static UI has no test runner and CI stays browserless per
ADR-006); re-running it is a manual step. Live *external*-URL conversion through
the UI remains gated on an environment with direct egress (Phase 5 / Docker),
same as the backend.
