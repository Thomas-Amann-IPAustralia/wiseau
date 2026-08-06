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
- [x] Autonomous ingestion example: scheduled diff-checking against saved snapshots. —
  *`backend/monitor.py`: a zero-dependency (stdlib-only) thin HTTP client over
  `POST /convert/url` that snapshots each URL's Markdown and diffs fresh
  conversions against the last one. Statuses: `new`/`unchanged`/`changed` (unified
  diff; content drift is expected, not an error, per §7) / `error`. CLI does a
  single pass or `--watch --interval N`. Inherits the backend's rate-limit +
  concurrency guards (invariant #4). Verified: 16 unit tests plus a real end-to-end
  run against a stdlib stub server (new → unchanged → changed-with-diff → error);
  see ADR-010. This closes Phase 4.*
- [x] *(2026-07-30 addendum)* MCP server reachable by remote/hosted clients, not
  just local ones. — `mcp_server.py` gained `--transport streamable-http`
  alongside the existing `stdio` default, so any MCP client that speaks HTTP —
  not only one that can spawn the process locally — can use wiseau as a
  connector. Same tool surface and contract; see ADR-028 and `docs/mcp.md`.

## Phase 9 — One deployable service any LLM can connect to (2026-07-30)

Design & rationale: **ADR-029** (amends ADR-028). Guide: `docs/hosting.md`.

- [x] **The backend serves the MCP endpoint itself.** `mcp_server.hosted_routes()`
  is grafted onto the FastAPI app and its session manager driven from the app
  lifespan, so one container answers the REST API *and* `POST /mcp` on one port —
  no second service to deploy on hosts that expose exactly one. Grafted rather
  than mounted so the exact `/mcp` a connector is given answers instead of
  redirecting; sessions stateless for scale-to-zero hosts; tools still call the
  API over loopback HTTP, keeping invariant #4. *Verified: a real MCP handshake +
  `tools/list` + a `convert_file` tool call converting a real PDF, driven over a
  socket against a live uvicorn from a public-looking Host header, and separately
  through the **official MCP client SDK** (default path, secret path, and mount
  disabled). 28 new tests.*
- [x] **The endpoint is rate-limited like any other route.** slowapi identifies a
  route by `endpoint.__name__`, which an ASGI object lacks — without a shim the
  middleware raised before checking any limit and every MCP request was a 500.
  *Found by the live check, not the unit suite; now pinned by a test.*
- [x] **The Host check no longer blocks a real deployment.** `WISEAU_MCP_ALLOWED_HOSTS`
  unset (or `*`) disables it — loopback-only was right for a local process and
  wrong for an endpoint meant to be reached at a public hostname; naming hosts
  enforces them.
- [x] **`WISEAU_MCP_PATH`** moves the endpoint to an unguessable path, the only
  access control every MCP client can express. `GET /ping` reports it as
  `mcp_endpoint` so a client discovers rather than guesses. API `0.7.0 → 0.8.0`.
- [x] **The image is deployable anywhere.** It installs `requirements-mcp.txt` and
  binds `$PORT`, so Cloud Run (8080) and Render (10000) work unchanged alongside
  Spaces (7860). CI's `docker-build` job now runs an MCP handshake against the
  built container.
- [x] **`docs/hosting.md`** — click-by-click deployment (Cloud Run, with Render and
  Spaces as alternatives), cost guards, verification, per-client connector setup,
  and an honest account of what is and isn't protected.
- [ ] **Deploy it.** Account work; the guide is the checklist. Then confirm a real
  LLM client round-trips a tool call against the live URL — the one thing no
  session so far has been able to prove.

## Phase 5 — Containerization & deployment

- [x] `Dockerfile` version-locking Chromium + Python — *built and run this
  session; pinned deps resolve, image boots, `/ping` serves `v0.2.0`. Also built
  in CI (`docker-build` job). See ADR-011.*
- [x] Build the image and confirm Chromium launches inside the container. —
  *verified: the image builds from the committed `Dockerfile`; inside the
  container Chromium **150** + ChromeDriver **150** launch and a real external URL
  renders end-to-end through `POST /convert/url` (example.com → clean Markdown;
  a Wikipedia article → ~30 KB structured Markdown; byte-identical SHA-256 across
  two runs → deterministic). This also closes Phase 2's live *external*-URL
  gap — headless Chrome rendered arbitrary internet pages, not just a `data:` URL.
  The CI `docker-build` job now re-proves this on every backend change. ADR-011.*
- [x] Prepare both deployments so nothing but account work is left (ADR-023). —
  *`backend/README.md` now carries the HF Space card frontmatter (`sdk: docker`,
  `app_port: 7860`) a Docker Space reads its configuration from, without which
  Space #1 would not have built; `.github/workflows/deploy-frontend.yml`
  publishes `frontend/` to Pages, which branch publishing cannot do (it serves a
  repo root or `/docs`, and `/docs` is the documentation). Written, not run —
  no Space and no Pages site exist yet.*
- [ ] Deploy the backend to a public host. *Any host that can build the
  Dockerfile and give it an HTTPS URL: Cloud Run, Render, or a Hugging Face Space
  (push the contents of `backend/` to the Space repo root). Click-by-click in
  `docs/hosting.md`; Space-specific steps in `backend/README.md`. Since ADR-029
  this one service also carries the MCP connector URL.*
- [ ] Point the frontend at the live Space: set the `MARKDOWN_API_BASE`
  **repository variable** (the Pages workflow writes it into the published
  `config.js`), or edit `frontend/config.js` for a non-Pages host.
- [ ] Deploy frontend via GitHub Pages: set **Settings → Pages → Source** to
  *GitHub Actions*, then run the **Deploy frontend** workflow.
- [ ] Confirm the deployed UI talks to the deployed backend end-to-end.

## Phase 6 — Higher-fidelity extraction via docling (selectable engine)

Design & rationale: **ADR-013** (fidelity outranks strict determinism where
fidelity is asked for), **ADR-014** (the docling engine + automatic
PyMuPDF/Mammoth fallback), **ADR-015** (docling-serve as an internal
microservice; free two-Space topology), **ADR-027** (the *default* engine is the
fast local parser; docling is selected per deployment or per request). Goal: a
live paste/upload → convert experience that can use docling for faithful Markdown
of complex/scanned government documents, degrading gracefully to the existing
parsers when docling is unavailable. The deploy tasks below extend Phase 5.

> **Amended 2026-07-30 (ADR-027).** The tasks below were written when docling was
> the default engine and are ticked as they were built; the engine layer is
> unchanged. What changed is which engine `WISEAU_PDF_ENGINE` names when nobody
> sets it: `pymupdf`. Read "docling-first" below as "docling when selected".

**Backend — docling client + engine selection**
- [x] `parsers/docling_client.py` — thin HTTP client to docling-serve
  (`WISEAU_DOCLING_BASE`), bearer `WISEAU_DOCLING_TOKEN`. Sends document bytes,
  requests `md`, returns Markdown. Bounded timeout (`WISEAU_DOCLING_TIMEOUT`) and
  typed errors (`DoclingUnavailable` vs `DoclingBadDocument`) so the caller can
  distinguish "docling down" from "bad document". *Built on stdlib `urllib` (not
  `httpx`) so the runtime image gains no dependency — ADR-016. Verified: 18 tests
  over a mocked transport (success, request shape, auth header, and every failure
  mode's typed error).*
- [x] Engine selection in `parsers/file_parser.py` — `WISEAU_PDF_ENGINE`
  (default `docling`; `docling` | `pymupdf`). Tries docling first (only when
  selected **and** `WISEAU_DOCLING_BASE` is set); on any `DoclingError`
  (connection error / timeout / 5xx / empty / 4xx), **logs and falls back** to
  PyMuPDF4LLM+OCR (PDF/image) or Mammoth (DOCX). Output still flows through
  `clean_markdown()`. *Verified: 6 engine-selection tests (docling chosen by
  default; skipped when unconfigured; `pymupdf` pins the local path; fallback on
  both error kinds; unsupported-type 415 preserved). With no base set — the
  default — behaviour is byte-identical to pre-Phase-6, so the existing suite is
  unaffected.*
- [x] Keep the docling call inside `main.py`'s `_job_semaphore` (invariant #4)
  — no new endpoint or entrypoint was added: docling runs inside the existing
  `file_to_markdown`, which `/convert/file` still calls inside `_job_semaphore`
  (unchanged in `main.py`). The guard wraps the new path unchanged.
- [x] PDF-typed **URL** fetches: when the headless browser retrieves a PDF (many
  government links are direct PDFs), route those bytes to docling too; HTML pages
  stay on the browser-render → Trafilatura path. *Done (ADR-017): Chrome's PDF
  viewer DOM (or a `.pdf` path) triggers `browser.fetch_bytes`, which downloads
  from inside the already-navigated page so the WAF clearance carries over; bytes
  are accepted only if they start with `%PDF-`, then handed to `file_to_markdown`
  (docling-first, auto-fallback, cleaned). Verified two ways: 20 tests over a faked
  driver, **and a live run** — real headless Chromium against a locally served PDF
  recovered its full text (reproducibly), while an HTML page on the same server
  stayed on the Trafilatura path.*
- [x] Isolate deps: the wiseau image needs **no torch** — the docling client is
  stdlib-only (`urllib`), so **`requirements.txt` is untouched** (no docling, no
  PyTorch, and no new `httpx` runtime dep). See ADR-016.

**docling-serve — the converter Space**
- [~] `docling/Dockerfile` (or the official `docling-serve` image) pinning
  docling-serve **and the model revision**; pre-download weights at build
  (`docling-tools models download`); run as UID 1000 on port 7860 (HF Spaces).
  *Written (ADR-018): `FROM ghcr.io/docling-project/docling-serve-cpu:v1.27.0@sha256:a70cd391…`
  — the upstream CPU image already bakes the weights in, so no boot-time download.
  Re-homed for Spaces (port 7860, UID 1000, writable state under `/tmp`).
  **Not built** — no Docker daemon in the session that wrote it; only the digest
  was checked against the registry.*
- [~] Own low concurrency cap (1–2) sized for 2 vCPU / 16 GB; reject/queue rather
  than OOM. Health endpoint for the warm-ping. *Written: one worker sharing one
  copy of the models (`ENG_LOC_NUM_WORKERS=1`, `ENG_LOC_SHARE_MODELS=true`),
  `LOAD_MODELS_AT_BOOT`, page/size caps, and `MAX_SYNC_WAIT=100` so docling's 504
  precedes the backend's 120s timeout. `/health` is wired as the HEALTHCHECK.
  Unverified until the image is built.*
- [x] Internal-auth check: require `WISEAU_DOCLING_TOKEN` (bearer) and/or make the
  Space private so only the wiseau backend can call it. *Both layers supported and
  unit-tested: `WISEAU_DOCLING_TOKEN` → `Authorization: Bearer` (private-Space
  gateway) and `WISEAU_DOCLING_API_KEY` → `X-Api-Key` (docling-serve's own
  `DOCLING_SERVE_API_KEY`, which ADR-015 had missed). Neither secret is baked into
  the image; 401/403/429 now classify as `DoclingUnavailable` (ADR-018).*

**Tests (stay browserless + docling-serve-less in default CI)**
- [x] `tests/test_docling_client.py` over a mocked HTTP transport: success →
  Markdown; timeout / 5xx / empty / non-JSON → `DoclingUnavailable`; 4xx →
  `DoclingBadDocument`; auth header sent; request shape (endpoint, multipart body,
  `to_formats=md`) asserted. 18 tests.
- [x] Engine-selection tests in `test_file_parser.py`: docling path chosen by
  default; skipped when unconfigured; `pymupdf` pins the local path; fallback on
  simulated docling failure (both error kinds); `clean_markdown()` still applied;
  unsupported-type 415 preserved. 6 tests.
- [x] `tests/test_url_parser.py` over a faked driver: an HTML page keeps the
  Trafilatura path (and is never probed for a download); Chrome's PDF-viewer DOM
  and `.pdf` URLs route into the document pipeline; URL-fetched PDFs get the
  docling-first treatment *and* fall back when docling is down; non-PDF bytes fall
  through to HTML; an unreachable viewer PDF raises; download failures
  (script error / undecodable / empty) degrade to `None`; the driver is always
  released; filename derivation is sanitized and bounded. 20 tests.

**Config, docs, deploy**
- [x] tech-spec: env vars in §5, topology in §8, and §11 "Extraction engine
  selection & fallback" — all present; §11's status banner now reflects the built
  backend. `mcp.md` unchanged (the contract is unchanged, so the agent surface is
  unaffected). Module table (§4) already lists `docling_client.py`.
- [ ] Deploy HF Space #2 (docling-serve); set `WISEAU_DOCLING_BASE` +
  `WISEAU_DOCLING_TOKEN` on Space #1. Verify **live**: a paste (HTML article), an
  **upload of a table-heavy / scanned government PDF** (docling fidelity vs the
  old path), and **fallback** (stop Space #2 → PyMuPDF still returns a result).
- [x] A log/metric of docling-vs-fallback usage so silent outages are visible. —
  *done as part of the observability work: `GET /metrics` reports
  `engines.docling` against `engines.pymupdf`/`mammoth` plus docling
  `successes`/`fallbacks`/`skipped` and the typed reason for each fallback, and
  every fallback also emits a structured WARNING. Verified live against both a
  stub docling-serve and a dead one. See ADR-019 and tech-spec §12.*
- [ ] Optional: a warm-ping (frontend or monitor) to keep the docling Space awake.
  *Deferred until Space #2 exists — the right interval can only be set against a
  real cold-start time.*

---

## Phase 7 — Usability pass: engine choice, progress, viewer, downloads

Driven by real use of the deployed-shaped UI. Design & rationale: **ADR-024**
(inlined base64 images), **ADR-025** (per-request engine), **ADR-026** (UI).

- [x] **Per-request engine selection.** `engine` (`docling` | `pymupdf` | `auto`)
  on both convert endpoints — JSON field on `/convert/url`, form field on
  `/convert/file` — plus an argument on both MCP tools; `GET /ping` advertises
  the accepted names. Unknown value ⇒ **400**, never a silent substitution.
  `WISEAU_PDF_ENGINE` remains the deployment *default*. API `0.5.0 → 0.6.0`
  (additive). *Verified: 14 backend tests (validation/normalization, override in
  both directions, an explicitly requested docling still falling back, 400s
  before any conversion starts, the OpenAPI surface, the MCP forwarding), and
  live in the browser — the chosen engine arrives on both endpoints.*
- [x] **Engine picker in the UI, with the slowness stated.** Three options and a
  note that docling takes tens of seconds to minutes on free CPU (longer cold)
  and falls back automatically. *Verified in a real browser.*
- [x] **Loading animation.** The API has no progress channel (one blocking call),
  so the bar is **approximated** client-side from source type, file size, and
  engine, on an asymptotic curve that never claims to be finished, with an
  elapsed timer and a "still working" message past 1.3× the estimate (ADR-026,
  tech-spec §14). *Verified: the bar appears, advances, and clears on both
  success and error.*
- [x] **Preview / raw viewer.** `frontend/markdown.js` — a ~200-line
  dependency-free renderer for the subset this engine emits; untrusted content is
  escaped before any markup is added, link schemes are restricted, and `data:`
  images render as a placeholder chip. *Verified in a real browser: headings,
  bold, lists, tables, code blocks, blockquotes, safe links, the placeholder
  chip, and that an embedded `<script>` renders as text and does not execute.*
- [x] **Favicon** (`frontend/favicon.svg`, linked from `index.html`) — served by
  the Pages workflow with the rest of `frontend/`. *Verified: fetched 200 and
  linked from the page.*
- [x] **Title-first download.** The Download button opens a dialog pre-filled
  from the document's first `#` (or first `##`) heading, editable, with a live
  filename preview and a *Confirm download*. *Verified: H1 title, H2 fallback,
  an amended title changing the saved filename, cancel not downloading.*
- [x] **Inlined base64 images no longer bloat the output.** Fixed at the source
  (a DOCX image handler that emits no data URI; `image_export_mode=placeholder`
  on docling requests) and at the exit (`clean_markdown` elides any base64
  data-URI payload, keeping the media type). *Verified: 8 cleaner tests, a
  DOCX-with-picture round trip, the docling request shape, and end-to-end through
  the real backend + UI — an illustrated DOCX converts to 103 characters with no
  payload in sight.*

---

## Phase 8 — Fast by default (2026-07-30)

Design & rationale: **ADR-027** (amends ADR-013/014).

- [x] **`WISEAU_PDF_ENGINE` defaults to `pymupdf`.** The fast, deterministic local
  parser is what an unconfigured deployment runs; docling is selected per
  deployment (`WISEAU_PDF_ENGINE=docling`) or per request (`engine="docling"`,
  ADR-025), with ADR-014's automatic fallback unchanged in both cases. *Verified:
  a configured-but-unselected docling is never called (new test), the metric reads
  `engine_not_selected`, and live through a real uvicorn with
  `WISEAU_DOCLING_BASE` pointed at a dead port — the default upload converted
  immediately via `pymupdf` with no docling attempt, while the same upload with
  `engine=docling` attempted docling and fell back.*
- [x] **`GET /ping` reports `default_engine`.** "Auto" resolves server-side, so a
  client can no longer assume what it means; `/ping` now says which engine will
  run, reported as behaviour (anything but `docling` ⇒ `pymupdf`). API
  `0.6.0 → 0.7.0` (additive field; the bump also marks the behaviour change).
  *Verified: 5 tests (the endpoint under both settings, `default_engine()` over
  unset/`docling`/`pymupdf`/nonsense) + the live `/ping` above.*
- [x] **The UI leads with the fast engine.** *Fastest* is listed ahead of
  *Highest fidelity*, the note says which is the default and when to reach for
  docling, and the *Auto* option is labelled from `/ping`'s `default_engine` —
  which also sizes the progress estimate, instead of always assuming docling.

---

## Phase 10 — One file per chapter (2026-08-06)

Design & rationale: **ADR-030**. Contract: `tech-spec.md` §15.

- [x] **`parsers/chapters.py` — chapter detection.** Splits *converted* Markdown
  (so it works the same for docling, PyMuPDF, Mammoth, and OCR output) with a
  cascade: the document's **contents page** first — the author's own list of
  chapters, matched forward into the body and believed only at ≥50% coverage —
  then **heading structure**, then plain-text **"Chapter N" markers**, then
  **nothing**. Deterministic: regexes and difflib, no model. *Verified: 30 unit
  tests, one per document shape, including the ones that must **not** split (an
  article, a back-of-book index, headings inside a code fence, a page of
  fragments).*
- [x] **The chapters partition the document.** Concatenating them reproduces the
  input, so a wrong boundary can misplace text but never lose it; front matter
  (title page + contents) is its own leading section unless it is a bare title
  line. *Verified: a partition test in the unit suite and again on a real
  26-page book PDF through a live server.*
- [x] **`split_chapters` on both convert endpoints** (default false; `chapters` +
  `chapter_detection` are `null` unless asked for, so an existing client sees an
  unchanged response). Each chapter carries a zero-padded `filename`, so saving
  one file per chapter is a loop. API `0.8.0 → 0.9.0` (additive). *Verified: 6
  API tests + a real 26-page PDF through uvicorn — 9 chapters via `toc`, lossless,
  byte-identical across two requests.*
- [x] **The same flag on both MCP tools**, so an agent asked to "split this book
  into files" gets the chapters and their filenames rather than having to invent
  boundaries. *Verified: 4 tests + a live call against a running backend.*
- [x] **A broken split cannot lose a good conversion.** An exception in the
  heuristics returns the document with `chapter_detection: "error"` (logged,
  counted) instead of a 502, and the split runs inside the job slot so it queues
  behind the same concurrency ceiling as the conversion. *Verified by a test that
  makes the splitter throw.*
- [x] **`GET /metrics` counts splits by method** (`toc`/`headings`/`markers`/
  `none`), because a heuristic that quietly stops finding anything looks exactly
  like nobody asking.
- [x] **The UI: a chapter panel and a ZIP.** *Split into chapters* checkbox; a
  panel listing every chapter with its filename, size, and what found it; **View** a
  chapter (Copy/Download then act on it), **Save** one, or **Download all
  (.zip)**. The archive is built in the browser by a new dependency-free
  `frontend/zip.js` (stored entries, fixed 1980 timestamp ⇒ byte-identical
  archives), keeping ADR-004. *Verified: 25 checks in headless Chromium against a
  stub, then the real UI against a live backend uploading the 26-page book — the
  downloaded ZIP passes `unzip -t` and every entry is byte-identical to the API's
  chapter.*
- [ ] **Watch it against real documents.** The heuristics are tuned on generated
  and hand-written fixtures. The next instance with real scanned government PDFs
  should check `/metrics`'s `by_method` against what those documents actually
  are, and tune `_MIN_TOC_MATCH_RATIO` / `_MIN_MEDIAN_CHAPTER_CHARS` if `none` is
  the usual answer.

---

## Cross-cutting backlog (not phase-bound)

- [x] **OCR for scanned / handwritten documents.** Image-only PDF pages and image
  uploads (`.png/.jpg/.tif/...`) are OCR'd; detection is per-page so mixed PDFs use
  native text where possible and OCR only scanned pages, assembled in order.
  Default engine is MuPDF's built-in Tesseract (deterministic, system-binary only);
  a neural handwriting engine (EasyOCR) is opt-in via `WISEAU_OCR_ENGINE=easyocr` +
  `requirements-ocr.txt`. Determinism preserved by pinning `pymupdf4llm`'s legacy
  extractor and driving MuPDF's OCR primitive directly (the 1.28 layout/OCR engine
  drops content non-deterministically across calls). Verified: 13 OCR tests (real
  Tesseract, in-memory scanned/image fixtures, stable across repeated runs) + HTTP
  round-trip; CI installs Tesseract and OCRs a page inside the built image. API
  `0.2.0 → 0.3.0`. See ADR-012 and tech-spec §10.

- [~] **Test suite.** Browser-free deterministic units (53 passing, 2 skipped):
  `cleaner` normalization/determinism, `file_parser` dispatch + real PDF **and
  DOCX** round-trips, request validation & error codes, `/convert/url` with a
  mocked driver, PDF/DOCX HTTP happy-paths, the **MCP tool surface**
  (`test_mcp_server.py`: contract round-trip, multipart forwarding, error-detail
  surfacing, tool registration — over a mocked HTTP transport), and the
  **autonomous-ingestion monitor** (`test_monitor.py`: new/unchanged/changed/error
  state machine, snapshot round-trip, diff determinism, real `urllib` request
  building, bounded watch loop). An opt-in live-browser test
  (`WISEAU_LIVE_BROWSER=1`) covers the real render→extract→clean pipeline, and the
  CI `docker-build` job renders a live **external** URL through the container.
  *Remaining:* none — both the Docker image build step in CI and live
  external-URL verification are done (ADR-011).
- [x] **CI.** GitHub Actions (`.github/workflows/backend-tests.yml`) has two jobs:
  `test` installs deps and runs `pytest` on `backend/**` changes (browserless), and
  `docker-build` builds the image, boots the container, and renders a live external
  URL end-to-end (Chromium runs for real on the Docker-capable, direct-egress
  runner). Docker image build step is done (ADR-011).
- [x] **Dependency pinning** across `requirements.txt`.
- [x] **Observability.** `backend/observability.py` + `GET /metrics` (API
  `0.3.0 → 0.4.0`). Structured JSON request logging with a correlation id
  (returned as `X-Request-ID`; uvicorn's duplicate access log switched off), job
  queue-wait/duration/peak-concurrency and process RSS for sizing
  `MAX_CONCURRENT_JOBS`, and **engine attribution** so a silent docling outage is
  visible: every conversion is counted against the engine that produced it, with
  docling's successes / fallbacks-by-reason / skips counted apart. Stdlib-only,
  no new runtime dependency (ADR-019). Verified: 19 registry tests + 8 HTTP tests
  + 4 attribution tests, and live — real requests through uvicorn, a real
  fallback with docling pointed at a dead port, and the JSON log lines. CI now
  asserts the metrics surface inside the built image. tech-spec §12.
- [x] **Trafilatura duplicates the body of very small documents.** Root-caused and
  repaired (ADR-020). Not a size quirk: when Trafilatura's extraction yields under
  `MIN_EXTRACTED_SIZE` (250 chars), `extract_content` calls `recover_wild_text`,
  which *extends* the already-populated body with every `<p>`/`<table>` it finds —
  re-adding what was already extracted. `url_parser._drop_repeated_run` drops the
  exact adjacent repeat, guarded to short documents and substantial runs so a
  healthy page is untouched. Verified live: the same headless-Chromium render of a
  short notice page returns the body twice before the repair and once after; 8
  tests, plus a CI assertion on the real example.com render. **Guard corrected
  (ADR-022):** the original bound was a block count, which does not track the
  250-char extraction the bug actually needs — an 851-char document lost a
  legitimately repeated paragraph. The repair is now bounded by the size of the
  *repeat* (40–250 chars), which is what the upstream rescue can produce.
- [x] **Code-review pass: fix the guards that were not guarding.** Six defects
  found by reading the backend against its own spec, all fixed and regression-tested
  (API `0.4.0 → 0.5.0`). (1) `Limiter(default_limits=...)` bound *nothing* —
  slowapi enforces the defaults only from `SlowAPIMiddleware`, so `/metrics` and
  every other undecorated route were unlimited and `@limiter.exempt` was a no-op
  (invariant #4). (2) The short-page repair could delete legitimate content from
  documents the upstream bug cannot affect (ADR-022). (3) `monitor.check_url`
  crashed the watch loop on a read timeout instead of reporting `error`, because
  urllib does not wrap that in `URLError`. (4) Oversized uploads were fully
  buffered in memory before the 413. (5) A scanned PDF was attributed to
  `pymupdf`, hiding the OCR path from `/metrics` entirely. (6) The PDF round-trip
  test's 14-char fixture sat under the 16-char OCR threshold, so it silently tested
  OCR rather than native extraction. Suite 147 → 176 (181 with the live-browser
  opt-in).
- [ ] **Abuse controls beyond rate limiting** (per-IP daily quota, optional API
  key tier) — only if fair-use limiting proves insufficient (see brief §7).
- [x] **SSRF exposure on `/convert/url`.** The public endpoint rendered any host
  from inside the container — loopback, RFC-1918, and `169.254.169.254` included.
  Addresses are now resolved and vetted before the browser starts; refusal is a
  400, and `WISEAU_ALLOW_PRIVATE_URLS=1` opts a self-hosted intranet deployment
  back in. Redirect-to-private and DNS rebinding remain open by design and need a
  network-layer egress control (ADR-021, tech-spec §13).

---

## How to use this file

1. Pick the next open (`[ ]`) task, preferring the "Suggested next actions" in
   `STATUS.md`.
2. Mark it `[~]` and note you're on it.
3. When done **and verified**, mark it `[x]`, update `STATUS.md`, and record any
   decision made along the way in `decisions.md`.
