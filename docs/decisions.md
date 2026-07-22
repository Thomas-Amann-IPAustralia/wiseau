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

## ADR-013 — RapidOCR (layout-aware) as the default OCR engine
**Date:** 2026-07-22 · **Status:** Accepted (refines ADR-012's engine default)
**Context:** ADR-012 shipped OCR with MuPDF-Tesseract as the default. Tesseract is
deterministic and dependency-light but weak on real-world scans — flat reading
order, no region detection, poor on skew/noise/varied fonts. We wanted "generally
better" OCR without giving up determinism or bloating the free-tier image, and
explicitly wanted to keep it simple: a single better engine, **not** a
dual-engine consensus/confidence layer (considered and deferred — two engines
disagreeing yields a confidence signal, not a correction, and doubles cost).
Candidates: docTR/Surya (torch, heavy, CPU-slow, big image) vs RapidOCR
(ONNX Runtime, torch-free).
**Decision:** Make **RapidOCR** (`rapidocr-onnxruntime`) the default engine. It is
a detection + recognition pipeline that reads text region-by-region — better
accuracy and line structure than Tesseract on real scans — while staying
deterministic (fixed ONNX models, greedy decoding) and light (~190 MB of deps,
**no PyTorch**). Tesseract is demoted to a **zero-dependency fallback**: when
`rapidocr_onnxruntime` isn't importable, `file_parser._default_engine_name()`
selects `tesseract`, so a lean deploy still OCRs without a hard failure. EasyOCR
remains the opt-in handwriting engine. The Dockerfile adds `libgl1` +
`libglib2.0-0` (OpenCV's shared libs); `rapidocr-onnxruntime` + `onnxruntime` are
pinned in `requirements.txt` for output stability.
**Consequences:** Better default extraction on real scans, still deterministic
(verified stable across repeated runs; both engines covered by parametrized
tests). Image grows by RapidOCR's deps — acceptable (torch-free) and still
free-tier-friendly. Two watch-outs: (a) RapidOCR is detection-based and can
**over-segment trivial single-line images** (Tesseract reads those cleaner) —
fine on real multi-region docs, and either engine is one env var away; (b) this
buys better *recognition + line structure*, not full document-layout semantics
(headings/tables) — that would need docTR/Surya/PP-Structure or a doc VLM, a
heavier future step behind the same `OcrEngine` seam. `WISEAU_OCR_ENGINE`
overrides the default in either direction.

## ADR-012 — OCR for scanned/handwritten PDFs and images (deterministic, pluggable)
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** The engine only read a PDF's embedded text layer (`pymupdf4llm`), so
scanned and handwritten PDFs — which are just page images — converted to empty
Markdown. OCR was needed, and it had to honour invariant #1 (determinism) and
stay light enough for the free Hugging Face CPU tier the backend still has to be
deployed to. Investigation surfaced two traps in `pymupdf4llm` 1.28:
1. Its **new layout engine** (`use_layout(True)`, the default) accumulates
   cross-call process state that *non-deterministically drops content* — not just
   OCR text but sometimes a page's native text — once several varied documents
   pass through one worker. Fatal for determinism.
2. Its **built-in OCR integration** has the same instability (progressive
   word-dropping across calls). MuPDF's own OCR primitive
   (`Page.get_textpage_ocr`), by contrast, is clean and byte-reproducible.
**Decision:** Do OCR ourselves, deterministically, and route around both traps:
- Pin `pymupdf4llm.use_layout(False)` (stable legacy extractor) for native text.
- Detect image-only pages **per page** (`< 16` non-whitespace chars of embedded
  text); OCR only those, then assemble the document in page order. A fully
  digital PDF keeps the exact pre-OCR fast path; a fully scanned PDF is entirely
  OCR'd; mixed PDFs interleave. Image uploads (PNG/JPEG/TIFF/...) are re-wrapped
  as a one-page PDF and OCR'd the same way — new supported input types.
- Make the OCR engine **pluggable** (`parsers/ocr.py`). Default **tesseract**
  (MuPDF's built-in Tesseract via `get_textpage_ocr`; system binary only, no new
  Python dep, deterministic, strong on printed/scanned). Opt-in **easyocr**
  (`WISEAU_OCR_ENGINE=easyocr` + `requirements-ocr.txt`) is a neural engine that
  handles handwriting/noisy captures; PyTorch is kept out of the default image.
- Configuration via env: `WISEAU_OCR_MODE` (`auto`/`force`/`off`),
  `WISEAU_OCR_DPI` (fixed 300 for reproducibility), `WISEAU_OCR_LANG`,
  `WISEAU_OCR_ENGINE`. Output still flows through `clean_markdown` (invariant #3)
  and runs under the concurrency + rate-limit guards (invariant #4). API `0.2.0
  → 0.3.0` (additive: new input types, no response-shape change).
**Consequences:** Scanned/handwritten documents now convert. The default stays
tiny and deterministic (only `tesseract-ocr` + `tesseract-ocr-eng` added to the
image; tessdata is auto-discovered — no `TESSDATA_PREFIX` hardcode that could go
stale across distros). CI installs Tesseract so the OCR tests run for real, and
the `docker-build` job OCRs a page *inside the image* to prove the deployed
container can. Watch for: (a) genuine handwriting is only "very good" with the
EasyOCR engine — Tesseract alone is weak on cursive, and no self-hosted engine is
flawless; (b) if a future `pymupdf4llm` fixes the layout-engine instability,
revisit the `use_layout(False)` pin (it also forgoes the newer engine's richer
table handling); (c) OCR is memory/CPU-heavy at 300 DPI — it shares the existing
`_job_semaphore`, so tune `MAX_CONCURRENT_JOBS` if scans dominate traffic.

## ADR-011 — Docker image verified end-to-end; CI builds it and renders a live URL
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** Every prior session was blocked on the same two things: no Docker
daemon to build the image, and an egress proxy headless Chrome couldn't consume,
so a *live external* URL had never rendered through the containerized pipeline.
This session's environment had both a working Docker daemon and direct egress,
making the marquee Phase 5 / last-of-Phase-2 verification finally possible. One
sandbox wrinkle remained: outbound HTTPS is re-terminated by an egress gateway
presenting its own CA, which Chromium's NSS store doesn't trust by default (a
render returns the browser's "connection is not private" interstitial as the
page — proving the pipeline works, but not on real content).
**Decision:** Build the committed `backend/Dockerfile` and verify the whole path
inside the container — `/ping`, Chromium **150** + ChromeDriver **150** (matched
pair from apt), and `POST /convert/url` on real external URLs. The production
Dockerfile was kept clean; sandbox-only CA trust (pip build egress + a per-user
NSS import of the egress CA so Chromium renders real content) was applied through
a *throwaway* `Dockerfile.verify` / NSS import that were **deleted after
verification, not committed**. To make this a standing guarantee rather than a
one-off, add a `docker-build` job to `backend-tests.yml`: it builds the image,
starts the container, asserts `/ping`, checks the Chromium/ChromeDriver versions,
and does a real `POST /convert/url` on `https://example.com` asserting the
extracted `Example Domain` content — GitHub-hosted runners have a Docker daemon
and *direct* egress (no gateway), so no CA workaround is needed there.
**Consequences:** The image is proven to build from pinned deps and to render
real external pages deterministically (example.com → clean Markdown; a Wikipedia
article → ~30 KB of structured Markdown; byte-identical SHA-256 across two runs).
The container is deployment-ready for Hugging Face Spaces. CI now guards the heavy
browser path the unit suite mocks, closing the last cross-cutting test gap. Still
open (needs external accounts/credentials, not code): the actual HF Space deploy,
pointing `frontend/config.js` at it, and GitHub Pages — the remaining Phase 5
items. The one non-obvious gotcha for future local runs *in this sandbox*: a live
external HTTPS render returns the egress gateway's TLS interstitial unless the
gateway CA is imported into Chromium's NSS store (`~/.pki/nssdb`); this does not
apply to production or CI.

## ADR-010 — Autonomous ingestion as a zero-dependency HTTP client of the backend
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** The last open Phase 4 item is an autonomous-ingestion example:
scheduled diff-checking of a URL's Markdown against a saved snapshot. Two design
choices had to be settled. (1) *How does it reach the engine?* Mirroring ADR-009,
it could import the parsers in-process or hit the HTTP API — and invariant #4
(fair-use guards are unconditional) forces the same answer: go over HTTP so it is
rate-limited and concurrency-capped like any other client. (2) *What is a
"change"?* Determinism is per-input, not across time (tech-spec §7): a live page
legitimately drifts, so a content difference is an *expected, reportable outcome*,
not a failure — only being unable to obtain fresh Markdown (backend down / render
error) is an error.
**Decision:** Add `backend/monitor.py` as a **thin HTTP client** over
`POST /convert/url` at `WISEAU_API_BASE`, using **only the Python standard
library** (`urllib`, `difflib`, `hashlib`, `json`, `argparse`) — no new
dependency to install or pin, and it runs anywhere the backend URL is reachable.
A `SnapshotStore` persists the last-seen Markdown per URL as one JSON file
(`WISEAU_SNAPSHOT_DIR`, default `.wiseau-snapshots`). `check_url` returns a typed
`CheckResult` with status `new` (first sight → baseline saved), `unchanged`
(identical content), `changed` (a deterministic unified diff attached — content
drift, `ok` is still true), or `error` (fetch failed; the last good baseline is
left untouched so the next check diffs against it). A CLI runs a single pass or,
with `--watch --interval N`, a bounded/looping schedule; the diff is dateless so
the same before/after pair always yields the same output.
**Consequences:** One extraction path and one contract, guards always in force,
zero added dependencies. The monitor needs a reachable backend (documented, same
as the MCP server). Tested with an injected fake fetcher and a real `urllib` path
against a stdlib stub server (new/unchanged/changed/error, exit codes, bounded
watch loop) — 16 tests, no browser or external network. Scheduling itself (cron,
systemd timer, CI) is left to the operator; `--watch` is a self-contained example.
Completes Phase 4.

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
