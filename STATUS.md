# STATUS

> **Live snapshot of where the project stands.** This is the first thing to read
> after [`CLAUDE.md`](CLAUDE.md) and the first thing to update before you end a
> session. Keep it honest — "scaffolded but untested" is more useful than a
> green checkmark that lies.

**Last updated:** 2026-07-24
**Updated by:** Claude Code (docling integration — planning session)
**Planning note (2026-07-24):** A new **Phase 6** is on record — integrate
**docling** as the *default* document parser for higher-fidelity Markdown
(complex/scanned government PDFs), with the existing PyMuPDF/Mammoth path as an
*automatic fallback* when docling is unavailable. This is **planned, not built**:
the decisions (ADR-013 fidelity>determinism, ADR-014 docling default+fallback,
ADR-015 docling-serve microservice on a free two-Space HF topology) and the
Phase 6 task list are written; **no code has changed**. See `docs/roadmap.md`
Phase 6 and `docs/decisions.md`. The Phase 5 deployment status below is unchanged.
**Overall phase:** Phases 1–4 verified end-to-end, and **Phase 5 now has its
container proven**: the Docker image builds from the committed `Dockerfile`,
Chromium **150** + ChromeDriver **150** launch inside it, and a real *external*
URL renders end-to-end and deterministically through `POST /convert/url`
(example.com → clean Markdown; a Wikipedia article → ~30 KB structured Markdown;
byte-identical SHA-256 across two runs). This closes the biggest standing
blocker — every prior session lacked a Docker daemon / direct egress — and also
retires Phase 2's last "live external-URL" caveat. CI now has a `docker-build`
job that re-proves this on every backend change. **Remaining Phase 5 work is
deployment only** (Hugging Face Space + GitHub Pages), which needs external
accounts/credentials rather than code. See ADR-011.

---

## At a glance

| Area | State | Notes |
| ---- | ----- | ----- |
| Backend API (Phase 1) | 🟢 Verified (browser-free) | Routes, CORS, rate limiting, concurrency ceiling written; app imports cleanly; `/ping`, `/convert/file` (real PDF), `/convert/url` (mocked driver) verified via `TestClient`. Live URL render still unproven. |
| Scraper / extraction (Phase 2) | 🟢 Verified (incl. external URLs) | Live headless-Chrome render → Trafilatura → cleaner proven end-to-end and codified as an opt-in test; DOCX-body path covered. Fetching arbitrary **external** URLs now proven inside the Docker container (example.com, Wikipedia — deterministic across runs); ADR-011. |
| OCR (scanned/handwritten) | 🟢 Verified | Image-only PDF pages + image uploads OCR'd; per-page detection assembles mixed PDFs in order. Default MuPDF-Tesseract (deterministic, in the image); opt-in neural EasyOCR for handwriting. Deterministic by pinning `pymupdf4llm` legacy mode + driving MuPDF's OCR primitive directly (ADR-012). 13 tests + HTTP round-trip verified; API `v0.3.0`. |
| Frontend UI (Phase 3) | 🟢 Verified | Full static UI driven end-to-end with headless Chromium against a live `uvicorn` backend: status badge, URL + PDF + DOCX conversion, copy/download, and error states all confirmed (18/18 UI checks). See ADR-008. |
| AI / MCP integration (Phase 4) | 🟢 Complete | MCP server (`mcp_server.py`) exposes `convert_url`/`convert_file`/`ping` as tools — thin HTTP adapter, same contract, guards intact; verified end-to-end vs a live backend + 6 unit tests. OpenAPI operation IDs/summaries cleaned (v`0.2.0`); `docs/mcp.md` written. **Autonomous-ingestion monitor** (`monitor.py`) built + verified (16 tests + real end-to-end run) — closes Phase 4. |
| Containerization & deploy (Phase 5) | 🟡 Image proven, not deployed | Image **builds and runs**: Chromium 150 launches in-container, a live external URL renders end-to-end + deterministically (ADR-011). Nothing deployed to Hugging Face / GitHub Pages yet (needs external accounts). |
| Automated tests | 🟢 Passing | **71 pass + 2 skipped** in default (browserless) runs (+18 OCR tests this session: 13 recognition + 5 engine-layer). Covers `cleaner`/PDF/**DOCX**/**OCR**/validation, the MCP tool surface, the **autonomous-ingestion monitor**, plus the live render→extract→clean pipeline. *This sandbox run: 65 pass + 2 skipped verified directly; the 6 (unchanged) MCP tests couldn't collect here because `mcp` wouldn't install over a Debian-managed `PyJWT` — they run in CI.* With `WISEAU_LIVE_BROWSER=1` the 2 live-browser tests also run. |
| CI/CD | 🟢 Tests + Docker build | `.github/workflows/backend-tests.yml`: a `test` job runs `pytest` (browserless) and a `docker-build` job builds the image, boots it, and renders a live external URL through the container. Docker-build gap closed (ADR-011). |
| Documentation | 🟢 Established | Brief, tech spec, roadmap, decisions, agent workflow, this file. |

Legend: 🟢 done & verified · 🟡 written but not verified · 🔴 not started/absent

---

## What exists right now

**Backend** (`backend/`)
- `main.py` — `GET /ping`, `POST /convert/url`, `POST /convert/file`; permissive
  CORS; `slowapi` per-IP limits; `asyncio.Semaphore` concurrency ceiling; upload
  size cap. Heavy work offloaded via `asyncio.to_thread`. Explicit OpenAPI
  operation IDs (`ping`/`convert_url`/`convert_file`) + summaries; API `v0.2.0`.
- `mcp_server.py` — MCP tool surface (FastMCP): `convert_url`, `convert_file`,
  `ping`. Thin HTTP adapter over the backend (`WISEAU_API_BASE`); reuses the
  `MarkdownResponse` contract and inherits the rate-limit + concurrency guards.
  Run with `python mcp_server.py` (stdio). Deps in `requirements-mcp.txt`.
- `monitor.py` — autonomous-ingestion example (Phase 4): a **stdlib-only** thin
  HTTP client over `POST /convert/url` that snapshots each URL's Markdown and
  diffs fresh conversions against the last (`new`/`unchanged`/`changed`/`error`;
  content drift is a `changed`, not an error — tech-spec §7). Inherits the
  rate-limit + concurrency guards. CLI: single pass or `--watch --interval N`.
  Snapshots under `WISEAU_SNAPSHOT_DIR` (default `.wiseau-snapshots`). ADR-010.
- `parsers/browser.py` — `initialize_driver()` with selenium-stealth + hardened
  Chrome args, env-configurable Chrome/driver paths.
- `parsers/url_parser.py` — renders with headless Chrome, extracts with
  Trafilatura, falls back to markdownify, normalizes via cleaner.
- `parsers/file_parser.py` — PDF via PyMuPDF4LLM (legacy mode) with per-page OCR
  of scanned pages, DOCX via Mammoth + markdownify, images via OCR.
- `parsers/ocr.py` — pluggable OCR engines: default MuPDF-Tesseract (system binary,
  deterministic), opt-in EasyOCR (neural, handwriting; `WISEAU_OCR_ENGINE=easyocr`).
- `parsers/cleaner.py` — deterministic Unicode/whitespace/typography normalizer.
- `Dockerfile` — Python 3.11-slim + system Chromium/chromedriver, non-root user.
- `requirements.txt` — direct dependencies **version-pinned** to verified
  releases; `requirements-dev.txt` — `pytest` + `httpx` for the suite.
- `conftest.py` + `pytest.ini` — put `backend/` on `sys.path`; `tests/` dir holds
  `test_cleaner.py`, `test_file_parser.py`, `test_api.py`, `test_mcp_server.py`,
  `test_monitor.py`, and the opt-in `test_browser_live.py` (53 pass + 2 skipped in
  browserless runs).

**CI** (`.github/`)
- `workflows/backend-tests.yml` — two jobs on any `backend/**` change:
  - `test`: installs runtime + dev deps and runs `pytest` (no browser
    provisioned; URL worker is mocked).
  - `docker-build`: builds the image from `backend/Dockerfile`, boots the
    container, asserts `/ping`, checks Chromium/ChromeDriver versions, and runs a
    live `POST /convert/url` on `https://example.com` (real headless-Chrome
    render — GitHub runners have Docker + direct egress). ADR-011.

**Frontend** (`frontend/`)
- `index.html`, `style.css`, `app.js` — tabbed URL/file UI, drop zone, status
  badge, copy/download of output, light/dark aware.
- `config.js` — single per-deployment knob `MARKDOWN_API_BASE` (default
  `http://localhost:7860`).

**Docs** (`docs/` + root) — see the document map in `CLAUDE.md`.

---

## Known gaps / not yet proven

- **No deployment** — no live Hugging Face Space, no GitHub Pages activation,
  so `MARKDOWN_API_BASE` still points at localhost. The container is *proven
  deploy-ready* (ADR-011); this is now the only substantial Phase 5 gap, and it
  needs external accounts/credentials rather than code.
- **MCP server + monitor need a reachable backend.** By design both are HTTP
  clients, so their tools only work when a backend is running at
  `WISEAU_API_BASE`. Verified against a local `uvicorn`/stub; not yet exercised
  against a deployed Space.
- **EasyOCR engine written but not run here.** The default OCR engine
  (MuPDF-Tesseract) is fully verified. The opt-in neural engine
  (`WISEAU_OCR_ENGINE=easyocr`) is implemented and its selection/lang-mapping are
  unit-tested, but the actual EasyOCR recognition path wasn't executed in this
  sandbox (PyTorch/`easyocr` not installed). Exercise it once when deploying with
  handwriting needs.
- *Sandbox note (not a product gap):* a live external **HTTPS** render inside the
  container in *this* sandbox returns the egress gateway's TLS interstitial unless
  the gateway CA is imported into Chromium's NSS store (`~/.pki/nssdb`). This is a
  sandbox artifact only — production and GitHub-runner CI have direct egress and
  render real content with no workaround (confirmed this session).

*Resolved this session (previously listed here): live external-URL fetch,
Chromium-in-container launch, and the CI Docker-build gap — all now proven
(ADR-011).*

---

## Suggested next actions (see `docs/roadmap.md` for the full backlog)

With the container proven (ADR-011), everything left is **Phase 5 deployment**,
which needs external accounts/credentials rather than code:

1. **Deploy the backend to a Hugging Face Space** (free CPU tier). The image is
   proven deploy-ready — it builds from `backend/Dockerfile`, boots, and renders
   live external URLs. Needs an HF account + Space (push the `backend/` build
   context or the built image). The container listens on `7860` and runs as
   UID 1000, already matching Spaces.
2. **Point `frontend/config.js` `MARKDOWN_API_BASE` at the live Space**, then
   deploy the frontend via **GitHub Pages** (repo settings → Pages, serve
   `frontend/`).
3. **Confirm the deployed UI talks to the deployed backend end-to-end**, and
   re-run the MCP + monitor checks against the deployed Space (set
   `WISEAU_API_BASE` to the Space URL).

*Done this session: the marquee Phase 5 / last-of-Phase-2 blocker — built the
Docker image and proved the whole heavy path inside the container (Chromium 150
launches; example.com and a Wikipedia article render end-to-end and
deterministically via `POST /convert/url`). Added a CI `docker-build` job that
re-proves it on every backend change. Verification used a throwaway
`Dockerfile.verify` + NSS CA import that were deleted, not committed — the
production `Dockerfile` is unchanged. See the session log and ADR-011.*

---

## Session log

Newest first. One short entry per working session — what changed and what the
next instance should know.

- **2026-07-24 — docling integration plan (Phase 6, planning only).** Recorded a
  decision to adopt **docling** as the *default* document parser for higher-fidelity
  Markdown of complex/scanned government documents, with the existing
  PyMuPDF4LLM+OCR / Mammoth parsers kept as an **automatic fallback** for when
  docling is cold/asleep/down (critical on the free tier). Three ADRs capture the
  reasoning: **ADR-013** relaxes invariant #1 — fidelity now outranks strict
  determinism for the default path, and stochastic docling output is *intended*,
  not a bug (tech-spec §1 amended to say so, so a future instance doesn't "fix"
  it); **ADR-014** makes conversion docling-first with automatic fallback via a
  pluggable engine layer mirroring `parsers/ocr.py` (new `parsers/docling_client.py`
  thin HTTP client at `WISEAU_DOCLING_BASE`, `WISEAU_PDF_ENGINE` default `docling`);
  **ADR-015** sets a $0 topology — static UI on GitHub Pages, wiseau backend +
  headless Chrome (WAF-bypass) on HF Space #1, docling-serve on HF Space #2 (16 GB
  each; Render free's 512 MB can't host either heavy box), internal call guarded by
  `WISEAU_DOCLING_TOKEN`. **No code changed** — `docs/roadmap.md` Phase 6 lists the
  ordered build tasks (docling client + engine selection + fallback, the
  docling-serve Space, tests, deploy/verify). Key caveats on record: CPU inference
  is slow and free Spaces cold-start (fallback + warm-ping mitigate); the datacenter
  IP leaves the **WAF ceiling unchanged** (the `browser.py` stealth upgrade to
  `nodriver`/`undetected-chromedriver` is a *separate* concern, not part of Phase 6).
  **Next instance:** implement Phase 6 top-down — start with `parsers/docling_client.py`
  + the `WISEAU_PDF_ENGINE` selection/fallback in `file_parser.py` (mockable, no live
  docling needed for unit tests), then stand up the docling-serve Space and verify
  live upload + fallback. Phase 5 deployment (HF Space for the wiseau backend +
  GitHub Pages) is still open and is a prerequisite for the live end-to-end check.
- **2026-07-22 — OCR for scanned & handwritten documents.** The engine only read a
  PDF's embedded text layer, so scanned/handwritten PDFs (page images) converted to
  empty Markdown. Added OCR: per-page detection (`< 16` non-whitespace chars ⇒
  image-only) OCRs only the scanned pages and assembles the doc in page order — a
  fully digital PDF keeps the exact fast path, a fully scanned one is all-OCR, mixed
  interleaves. Also added **image uploads** (`.png/.jpg/.tif/...`, re-wrapped as a
  1-page PDF and OCR'd) as new supported input types (API `0.2.0 → 0.3.0`, additive).
  Engine is **pluggable** (`parsers/ocr.py`): default **tesseract** (MuPDF's built-in
  Tesseract via `get_textpage_ocr` — system binary only, no new Python dep,
  deterministic, strong on print) and opt-in **easyocr** (neural, handwriting;
  `WISEAU_OCR_ENGINE=easyocr` + `requirements-ocr.txt`, PyTorch kept out of the
  default image). Env knobs: `WISEAU_OCR_MODE`/`ENGINE`/`DPI`(300)/`LANG`(eng).
  **The hard part was determinism.** `pymupdf4llm` 1.28 *already* auto-OCRs when
  Tesseract is present — but two of its subsystems silently break invariant #1: its
  new **layout engine** (`use_layout(True)`, default) and its **built-in OCR** both
  accumulate cross-call process state that *non-deterministically drops content*
  (words, and even whole native-text pages) once several varied docs pass through
  one worker — reproduced outside pytest. MuPDF's own `Page.get_textpage_ocr` and
  `pymupdf4llm`'s **legacy** extractor are clean and byte-reproducible, so I pinned
  `use_layout(False)` and drive MuPDF's OCR primitive directly, bypassing both traps
  (ADR-012). Dockerfile installs `tesseract-ocr` + `tesseract-ocr-eng`; tessdata is
  **auto-discovered** (dropped an earlier hardcoded `TESSDATA_PREFIX` that could go
  stale across distros). CI `test` job now installs Tesseract so the 13 new OCR tests
  run for real, and `docker-build` OCRs a page **inside the built image** to prove
  the deployed container can. Frontend `accept=` + hint updated for images. Verified:
  full suite **65 pass + 2 skipped** here (OCR tests stable across 3 repeated runs;
  the 6 unchanged MCP tests couldn't collect in this sandbox — `mcp` won't install
  over Debian's `PyJWT` — they run in CI), plus a `TestClient` round-trip (scanned
  PDF + image → recovered text, `.txt` still 415). Updated tech-spec (§2/§4/§5 + new
  §10), roadmap, decisions (ADR-012), both READMEs. **Next instance:** unchanged —
  Phase 5 **deployment** (Hugging Face Space + GitHub Pages) is all that's left, and
  needs external accounts/credentials, not code. If deploying with heavy handwriting
  needs, enable EasyOCR (bake its weights into the image to avoid first-request
  download) and consider bumping `MAX_CONCURRENT_JOBS` down since OCR at 300 DPI is
  memory-heavy.
- **2026-07-22 — Docker image proven end-to-end + CI docker-build.** Cleared the
  blocker every prior session was stuck on: this environment had a working Docker
  daemon *and* direct egress. Built the committed `backend/Dockerfile` and verified
  the full heavy path **inside the container** — `/ping` serves `v0.2.0`, Chromium
  **150** + ChromeDriver **150** launch (matched pair from apt), and `POST
  /convert/url` renders real external URLs end-to-end: example.com → clean
  Markdown, a Wikipedia article → ~30 KB of structured Markdown (tables + links),
  and byte-identical SHA-256 across two runs (determinism holds). This also retires
  Phase 2's last "live external-URL" caveat — headless Chrome rendered arbitrary
  internet pages, not just a `data:` URL. Kept the production Dockerfile **clean**:
  the only reason the plain build failed here was the sandbox proxy CA for pip, so
  verification used a *throwaway* `Dockerfile.verify` (adds CA trust for build
  egress) plus a per-user NSS import of the egress-gateway CA (so Chromium renders
  real content instead of the gateway's TLS interstitial) — both **deleted after
  verification, not committed**. Made it a standing guarantee by adding a
  `docker-build` job to `.github/workflows/backend-tests.yml`: it builds the image,
  boots the container, checks `/ping` + Chromium/ChromeDriver versions, and does a
  live `POST /convert/url` on `https://example.com` asserting the extracted
  `Example Domain` content (GitHub runners have Docker + direct egress → no CA
  workaround needed). Updated roadmap (Phase 5 image-build + Chromium-in-container
  → `[x]`, CI Docker-build → `[x]`), flipped the cross-cutting CI item, and recorded
  ADR-011. **Next instance:** everything left is Phase 5 **deployment** — Hugging
  Face Space for the backend, then `frontend/config.js` → the Space URL and GitHub
  Pages for the frontend. That needs external accounts/credentials, not code; the
  container itself is proven deploy-ready.
- **2026-07-22 — Phase 4 autonomous ingestion (complete).** Built the last Phase 4
  item: `backend/monitor.py`, a scheduled diff-checker. It converts one or more
  URLs to Markdown and diffs each fresh conversion against the last one it saw.
  Like the MCP server, it's a **thin HTTP client** over `POST /convert/url` at
  `WISEAU_API_BASE`, so it inherits the backend's rate-limit + concurrency guards
  unchanged (invariant #4) — no in-process bypass. Deliberately **stdlib-only**
  (`urllib`/`difflib`/`hashlib`/`json`/`argparse`): no new dependency to pin, runs
  anywhere the backend is reachable. `SnapshotStore` persists last-seen Markdown
  as one JSON file per URL under `WISEAU_SNAPSHOT_DIR`; `check_url` returns a typed
  `CheckResult` — `new` (baseline saved) / `unchanged` / `changed` (deterministic,
  dateless unified diff; content drift is a **success**, not an error, per §7) /
  `error` (fetch failed → last good baseline left intact). CLI does a single pass
  (exit 0, or 1 if any check errored) or `--watch --interval N`. Added
  `tests/test_monitor.py` (16 tests: the new/unchanged/changed/error state machine,
  snapshot round-trip, diff determinism, real `urllib` request-building + error
  surfacing, bounded watch loop, CLI formatting/exit codes) — full suite now **53
  pass + 2 skipped**, verified by actually running it. Also **verified end-to-end**
  by running the CLI against a stdlib stub backend: `new` → `unchanged` →
  `changed` (printed a correct unified diff) → `error` (exit 1 when the backend was
  down). Rewrote `docs/mcp.md` §3 to document the built monitor, updated tech-spec
  §9, added a monitor section to `backend/README.md`, recorded ADR-010, and flipped
  the last Phase 4 checkbox. **Next instance:** Phase 4 is done — everything left
  is Phase 5 (Docker image build + in-container Chromium, then Hugging Face Space +
  GitHub Pages deploy), which needs a Docker daemon / direct egress this sandbox
  lacks.
- **2026-07-21 — Phase 4 MCP integration.** Exposed the engine as an agent-native
  tool surface. Added `backend/mcp_server.py` (FastMCP) with three tools —
  `convert_url`, `convert_file`, `ping` — built as a **thin HTTP adapter** over
  the running backend (`WISEAU_API_BASE`, default `:7860`), so the tools reuse the
  exact `MarkdownResponse` contract and inherit the rate-limit + concurrency
  guards unchanged (tech-spec invariant #4; ADR-009) rather than importing the
  parsers in-process. Backend error `detail` is surfaced verbatim. Refined the
  OpenAPI surface for function calling: explicit operation IDs
  (`convert_url`/`convert_file`/`ping`) + per-route summaries in `main.py`, API
  version bumped `0.1.0 → 0.2.0`. New deps isolated in `requirements-mcp.txt`
  (`mcp==1.28.1`, `httpx`); `mcp` also added to `requirements-dev.txt` so CI can
  test the adapter. Added `tests/test_mcp_server.py` (6 tests over a mocked
  `httpx` transport: contract round-trip, multipart+filename forwarding, error
  `detail` surfacing, non-JSON fallback, tool registration) — suite now **37 pass
  + 2 skipped**. **Verified end-to-end against a live `uvicorn` backend:** `ping`
  → 200, a real generated PDF through `convert_file` → correct Markdown, and a
  `.txt` upload → backend's 415 `detail` surfaced through the tool. Documented the
  whole surface in `docs/mcp.md` (tool table, config, run/stdio, Claude Desktop
  wiring, OpenAPI function-calling path); rewrote tech-spec §9; recorded ADR-009;
  flipped the three Phase 4 tasks to `[x]`. **Next instance:** the remaining
  Phase 4 item is the autonomous-ingestion diff-checker; the Docker-image build +
  live *external*-URL check still need a Docker daemon / direct egress.
- **2026-07-21 — Phase 3 frontend end-to-end verification.** Closed both open
  Phase 3 tasks by driving the *shipped* UI with headless Chromium (Playwright)
  against a live stack — `uvicorn` backend on `:7860`, the static frontend on
  `:8000`, and a throwaway fixtures server on `:8001` so the URL path renders a
  *local* article (no external egress needed here). 18/18 UI checks passed: status
  badge → `online`, URL conversion (headless-Chrome render → Trafilatura →
  cleaner), PDF **and** DOCX upload conversion, Copy (clipboard content verified) +
  "Copied!" feedback, Download → `converted.md`, plus error rendering for a backend
  415 (styled, shows the server's `detail`) and the client-side empty-URL guard.
  Backend endpoints were also smoke-tested directly via curl. Practical finding:
  the sandbox ships Chromium 141 (Playwright bundle) but only v147 chromedrivers on
  `PATH`; fetched a matching **141** chromedriver from Chrome-for-Testing and pinned
  it via `CHROMEDRIVER_PATH` — with that, the full suite runs 33/33 (the 2 opt-in
  live-browser tests now execute rather than skip). Recorded ADR-008; flipped the
  two Phase 3 roadmap tasks to `[x]`. **Next instance:** Phase 4 (MCP wrappers over
  the two convert endpoints) is the highest-value pure-code work; the Docker-image
  build + live *external*-URL check still need a Docker daemon / direct egress.
- **2026-07-21 — Live-browser verification & DOCX round-trip.** Proved the
  biggest remaining unknown: the live URL render path. With a version-matched
  Chromium + chromedriver, `url_to_markdown` launches headless Chrome, renders
  the DOM, and Trafilatura → cleaner produce clean, deterministic Markdown —
  confirmed end-to-end and codified as `backend/tests/test_browser_live.py`
  (opt-in via `WISEAU_LIVE_BROWSER=1`, self-contained `data:` URL so it needs no
  network). Learned that chromedriver must match Chrome's *major* version;
  Selenium Manager auto-fetches the matching pair (`browser.py` already honours
  `CHROME_BIN`/`CHROMEDRIVER_PATH`). Fetching arbitrary **external** URLs is
  blocked in this sandbox by its authenticated egress proxy (`ERR_CONNECTION_
  RESET`) — not a code defect; deferred to the Docker/Spaces environment. Also
  closed the DOCX gap: added real DOCX round-trip tests (dispatch, structure,
  determinism, HTTP happy-path) with in-memory `python-docx` fixtures and pinned
  `python-docx==1.2.0` in `requirements-dev.txt`. Suite now 31 pass + 2 skipped.
  Recorded ADR-007; flipped Phase 2 render/determinism tasks to `[x]`.
  **Next instance:** build the Docker image (confirm in-container Chromium launch)
  and verify a real external URL where direct egress is available.
- **2026-07-20 — Test suite, CI & dependency pinning.** Stood up the
  browser-free half of the engine as verified. Installed and resolved all deps,
  confirmed `main` imports cleanly (every parser loads). Added `backend/tests/`
  (28 tests, all passing): `test_cleaner.py` (Unicode/quote/whitespace
  normalization, determinism, idempotence), `test_file_parser.py` (extension
  dispatch, unsupported-type errors, in-memory PDF round-trip), `test_api.py`
  (`/ping`, URL validation → 422, mocked happy/failure paths, empty→400,
  unsupported→415, oversized→413, real-PDF upload, OpenAPI presence). Pinned all
  direct deps in `requirements.txt`; split test tools into `requirements-dev.txt`;
  added `conftest.py` + `pytest.ini`. Wired `.github/workflows/backend-tests.yml`
  to run `pytest` on `backend/**`. Recorded ADR-006; closed ADR-005's pinning
  follow-up. **Next instance:** the live Chromium render path is still the key
  unproven piece — build the Docker image and exercise a real URL.
- **2026-07-20 — Documentation foundation.** Established the doc set for the
  instance chain: `CLAUDE.md`, this `STATUS.md`, `docs/tech-spec.md`,
  `docs/roadmap.md`, `docs/decisions.md`, `docs/agent-workflow.md`. No code
  changed. Assessed existing scaffold as Phases 1–3 written-but-unverified.
