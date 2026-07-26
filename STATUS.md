# STATUS

> **Live snapshot of where the project stands.** This is the first thing to read
> after [`CLAUDE.md`](CLAUDE.md) and the first thing to update before you end a
> session. Keep it honest — "scaffolded but untested" is more useful than a
> green checkmark that lies.

**Last updated:** 2026-07-26
**Updated by:** Claude Code (operational-readiness pass: deployment prepared in-repo)
**Build note (2026-07-26, fifth session today):** **Everything left to reach
"operational" is now account work.** An audit of what deployment actually requires
found two blockers that were *code*, not accounts, and both would have failed on
the first attempt (ADR-023):

1. **Space #1 had no Space card.** A Hugging Face Docker Space reads its
   configuration from YAML frontmatter in the Space repo's `README.md`.
   `docling/README.md` had one; `backend/README.md` did not — no `sdk: docker`,
   no `app_port: 7860`. Added, plus a deployment section covering what to push
   and how to verify the Space is live.
2. **GitHub Pages could not have served `frontend/`.** Branch publishing serves a
   repository root or `/docs` only, and `/docs` is the project documentation — so
   "activate GitHub Pages" was not a setting anyone could switch on. Added
   `.github/workflows/deploy-frontend.yml` (`upload-pages-artifact` /
   `deploy-pages`, push-to-`main` on `frontend/**` plus manual dispatch).

The workflow also removes the last commit from the deployment path: with the
`MARKDOWN_API_BASE` **repository variable** set, it rewrites that assignment in
the *uploaded* `config.js` only, so the committed default stays `localhost` and a
public repo needn't carry the deployment's URL. A non-absolute value fails the job
rather than publishing a site that silently calls localhost. Verified by extracting
the step from the YAML and running it against a copy of the real `config.js`: unset
→ committed default untouched, set → rewritten with the trailing slash stripped,
malformed → exit 1. **Neither half is proven end-to-end** — no Space and no Pages
site exists, so frontmatter and workflow are written-but-unrun. No backend code
changed this session; the suite was not re-run (deps are not installed in this
sandbox), and the numbers below are the previous session's.

**The previous build note stands:** **A code-review pass over the
backend found six defects and one security exposure; all are fixed, regression-
tested, and verified live.** API `0.4.0 → 0.5.0`. The theme is guards that read as
present but bound nothing:

1. **The default rate limits applied to nothing.** `Limiter(default_limits=...)`
   only reaches undecorated routes through `SlowAPIMiddleware`, which was never
   installed — so `/metrics` (and `/openapi.json`, `/docs`) were completely
   unlimited and `@limiter.exempt` on `/ping` was a no-op, while the decorated
   `20/min` on the convert routes kept working and hid it. tech-spec §2/§5 and
   `backend/README.md` had documented the defaults as live for three sessions.
   Verified through real uvicorn: 60× 200 then 429 on `/metrics`, 70× 200 on
   `/ping`, and a middleware-issued 429 still carrying CORS + `X-Request-ID`.
2. **The short-page repair could delete real content** (ADR-022). Its only size
   guard was a block count, but Trafilatura's bug needs an extraction under 250
   *characters* — so an 851-char document (3.4× over the threshold, i.e. one the
   bug cannot have touched) lost a legitimately repeated paragraph. Now bounded by
   the size of the *repeat* (40–250 chars), which is what the upstream rescue can
   actually produce.
3. **`monitor.py` crashed instead of reporting an error.** urllib does not wrap a
   *read* timeout in `URLError`, so it surfaced as a bare `TimeoutError`, sailed
   past `check_url`'s handler and took the whole `--watch` loop down — on the most
   likely failure of all (a slow render plus a docling cold start). Every failure
   to obtain Markdown is a `MonitorError` again.
4. **Oversized uploads were fully buffered before the 413.** The comment said
   "reject before buffering"; the code read the whole body first. Now streamed
   with the ceiling enforced per chunk.
5. **Scanned PDFs were attributed to `pymupdf`**, so `engines.ocr` was structurally
   incapable of counting a document — the OCR path was invisible in `/metrics`.
   Each parser now records what it actually ran; verified live (a scanned upload
   reports `engines: {"ocr": 1}`).
6. **The PDF round-trip test tested OCR, not PDF extraction.** Its fixture string
   was 14 characters, under the 16-char image-only threshold, so the born-digital
   page was rasterized and OCR'd — passing for the wrong reason, and failing
   outright wherever `tesseract` is absent.

Plus **ADR-021**: `/convert/url` rendered any host from inside the container, which
is an SSRF primitive on a public endpoint (`127.0.0.1`, RFC-1918,
`169.254.169.254`). Addresses are now resolved and vetted before the browser
starts → **400**, with `WISEAU_ALLOW_PRIVATE_URLS=1` for self-hosted intranet use.
Redirect-to-private and DNS rebinding stay open **by design** — they need a
network-layer egress control, and the docs say so rather than overclaiming.

Suite **147 → 176 pass + 6 skipped** (181 with the live-browser opt-in, all 5 of
which were run and passed here against real Chromium 141 + a matched driver,
including a new test proving the guard refuses a real loopback URL). One flake to
know about: `test_live_render_extracts_heading_and_body` failed once on a full
live run and then passed 8 consecutive times (3 full runs + 5 of that module
alone). Not reproduced and not diagnosed — most likely a cold Chrome start
immediately after the browserless suite. Watch it; do not assume it is fixed. Also verified
through a real uvicorn: the rate limits above, the SSRF refusals, a loopback page
converting end-to-end with the short-page repair intact (one body copy), and the
corrected engine attribution. *Not* verified here: rendering an **external** URL —
this sandbox routes egress through a proxy the headless browser is not configured
for, so Chrome gets `ERR_CONNECTION_RESET`. CI's `docker-build` job covers that
path and is unaffected by these changes.

**The earlier build note stands:** **The cross-cutting backlog's
two open code items are done.** (1) **Observability** (ADR-019): structured JSON
logging with a per-request correlation id, and `GET /metrics` (API `0.4.0`)
reporting job timings, peak concurrency, RSS, and — the point of the exercise —
**engine attribution**, so a docling outage that ADR-014 turns into a *successful*
response is finally visible as `engines.docling: 0`. (2) **Trafilatura's
duplicated body** (ADR-020): root-caused to its `recover_wild_text` rescue, which
*extends* an already-populated body, so any page under its 250-char threshold
came back doubled — repaired in `url_parser`, verified live before/after against a
real headless-Chromium render. Both verified beyond unit tests: real requests
through uvicorn, a real docling fallback (docling pointed at a dead port) and a
real docling *success* against a loopback stub — the first time the docling client
has spoken over an actual socket rather than to an injected transport. Suite:
**147 pass + 5 skipped** (151 with the live-browser opt-in, all 4 of which were
run and passed here). The earlier build note stands otherwise:
**Phase 6's code is complete; only deployment is left.** Two things landed. (1) **Direct-PDF URLs** (ADR-017):
`/convert/url` no longer returns near-empty Markdown for a link that resolves to a
PDF — Chrome's PDF-viewer DOM (or a `.pdf` path) triggers a download *from inside
the already-navigated page* (`browser.fetch_bytes`), so the session's WAF
clearance and cookies carry over, and the bytes go through `file_to_markdown`,
i.e. the same docling-first pipeline an upload gets. **Verified live** against
real headless Chromium and a loopback-served PDF (full text recovered,
reproducible; an HTML page on the same server stayed on the Trafilatura path), and
codified as two more opt-in live tests plus 20 faked-driver tests. (2) The
**docling-serve Space** (`docling/Dockerfile` + `docling/README.md`, ADR-018):
the upstream CPU image, tag- *and* digest-pinned, weights already baked in,
re-homed for HF Spaces (UID 1000, port 7860, one worker sharing one model copy,
`MAX_SYNC_WAIT` ahead of the backend's timeout). Reading docling-serve's own docs
turned up an auth mechanism ADR-015 had missed — `DOCLING_SERVE_API_KEY` is an
`X-Api-Key` header, *not* the bearer token — so the client now sends both
credentials from separate env vars, and 401/403/429 are classified as
`DoclingUnavailable` (a deployment problem) rather than "bad document". Full
suite: **106 pass + 5 skipped** (110 with the live-browser opt-in). Still open in
Phase 6: **deployment only** — the docling image has never been built (no Docker
daemon this session; the pinned digest was verified against the registry), and no
Space exists yet. The Phase 5 deployment status is unchanged.
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
| Backend API (Phase 1) | 🟢 Verified (browser-free) | Routes, CORS, rate limiting, concurrency ceiling written; app imports cleanly; `/ping`, `/convert/file` (real PDF), `/convert/url` (mocked driver) verified via `TestClient`. Rate limiting **now actually enforced** on undecorated routes (`SlowAPIMiddleware` was missing, so the documented `60/min` + `1000/day` defaults bound nothing) and uploads are size-checked while streaming; both verified through a real uvicorn. API `v0.5.0`. |
| Scraper / extraction (Phase 2) | 🟢 Verified (incl. external URLs) | Live headless-Chrome render → Trafilatura → cleaner proven end-to-end and codified as an opt-in test; DOCX-body path covered. Fetching arbitrary **external** URLs now proven inside the Docker container (example.com, Wikipedia — deterministic across runs); ADR-011. **Direct-PDF links** now convert as documents rather than yielding the empty PDF viewer — verified live (ADR-017). **Short pages no longer come back with a duplicated body** (ADR-020), verified live before/after. |
| OCR (scanned/handwritten) | 🟢 Verified | Image-only PDF pages + image uploads OCR'd; per-page detection assembles mixed PDFs in order. Default MuPDF-Tesseract (deterministic, in the image); opt-in neural EasyOCR for handwriting. Deterministic by pinning `pymupdf4llm` legacy mode + driving MuPDF's OCR primitive directly (ADR-012). 13 tests + HTTP round-trip verified; API `v0.3.0`. |
| Frontend UI (Phase 3) | 🟢 Verified | Full static UI driven end-to-end with headless Chromium against a live `uvicorn` backend: status badge, URL + PDF + DOCX conversion, copy/download, and error states all confirmed (18/18 UI checks). See ADR-008. |
| AI / MCP integration (Phase 4) | 🟢 Complete | MCP server (`mcp_server.py`) exposes `convert_url`/`convert_file`/`ping` as tools — thin HTTP adapter, same contract, guards intact; verified end-to-end vs a live backend + 6 unit tests. OpenAPI operation IDs/summaries cleaned (v`0.2.0`); `docs/mcp.md` written. **Autonomous-ingestion monitor** (`monitor.py`) built + verified (16 tests + real end-to-end run) — closes Phase 4. |
| Containerization & deploy (Phase 5) | 🟡 Image proven + deploy prepared, not deployed | Image **builds and runs**: Chromium 150 launches in-container, a live external URL renders end-to-end + deterministically (ADR-011). Both deployments are now prepared in-repo — HF Space card frontmatter on `backend/README.md`, a Pages workflow for `frontend/` (ADR-023) — so what remains is account work only. Nothing deployed to Hugging Face / GitHub Pages yet. |
| Higher-fidelity extraction (Phase 6) | 🟡 Code complete, not deployed | docling client + docling-first engine selection with automatic fallback (ADR-014/016), **direct-PDF URL routing** (ADR-017, verified live), and the **docling Space image** `docling/Dockerfile` (ADR-018, digest-pinned but **never built**). Left: deploy Space #2 and verify against a live docling-serve. Fidelity outranks strict determinism (ADR-013). |
| Observability | 🟢 Verified | Structured JSON logs (one access line per request + `X-Request-ID`), `GET /metrics` with request/job timings, peak concurrency, RSS, and **engine attribution** (docling vs the fallback parsers, with typed fallback reasons). Stdlib-only, no new runtime dep. Verified live, incl. a real docling fallback and a real docling success over a socket. ADR-019, tech-spec §12. |
| Automated tests | 🟢 Passing | **176 pass + 6 skipped** in default (browserless) runs (this sandbox, verified directly). +29 this session (rate-limit enforcement + exemption + route attribution, streamed upload rejection, blocked-URL 400, 12 private-address guard cases, the repair's corrected size bound, native-vs-OCR path pinning, OCR engine attribution, 4 monitor failure modes). Covers `cleaner`/PDF/**DOCX**/**OCR**/**docling client & engine selection**/**direct-PDF URL routing**/**observability**/**fair-use guards**/**fetch-target policy**/validation, the MCP tool surface, the **autonomous-ingestion monitor**, plus the live render→extract→clean pipeline. Skips: 5 opt-in live-browser (`WISEAU_LIVE_BROWSER=1`; **all 5 run and passed here** with a version-matched Chromium 141 + driver → 181 total) + 1 OCR-fixture test needing Pillow. |
| CI/CD | 🟢 Tests + Docker build | `.github/workflows/backend-tests.yml`: a `test` job runs `pytest` (browserless) and a `docker-build` job builds the image, boots it, renders a live external URL through the container, and now also asserts the **short-page repair** on that real render, the **`/metrics` attribution**, and that request logs are structured JSON. Docker-build gap closed (ADR-011). A second workflow, `deploy-frontend.yml`, publishes `frontend/` to GitHub Pages (never run — Pages is not enabled yet; ADR-023). |
| Documentation | 🟢 Established | Brief, tech spec, roadmap, decisions, agent workflow, this file. |

Legend: 🟢 done & verified · 🟡 written but not verified · 🔴 not started/absent

---

## What exists right now

**Backend** (`backend/`)
- `main.py` — `GET /ping`, `GET /metrics`, `POST /convert/url`,
  `POST /convert/file`; permissive CORS; `slowapi` per-IP limits (decorator limits
  **plus** `SlowAPIMiddleware`, without which the defaults bind nothing);
  `asyncio.Semaphore` concurrency ceiling; upload size cap. Heavy work offloaded
  via `asyncio.to_thread`, inside a `_job_slot` wrapper that times the queue wait
  and the work without changing the guard. One structured access log line per
  request, with an `X-Request-ID` correlation id. Uploads are read in chunks with
  the size ceiling enforced mid-stream, so an oversized body is refused without
  being assembled in memory. Explicit OpenAPI operation IDs
  (`ping`/`metrics`/`convert_url`/`convert_file`) + summaries; API `v0.5.0`.
- `observability.py` — JSON-lines log formatter + the thread-safe in-process
  metrics registry behind `/metrics`. Stdlib only; a pure side channel that
  cannot alter extracted Markdown. ADR-019.
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
  Chrome args, env-configurable Chrome/driver paths; `fetch_bytes()` downloads a
  URL *through the driver's own session* (keeps WAF clearance) and returns `None`
  rather than raising on failure.
- `parsers/url_parser.py` — vets the target address first (ADR-021: non-public
  addresses are refused with a 400 unless `WISEAU_ALLOW_PRIVATE_URLS=1`), then
  renders with headless Chrome, extracts with
  Trafilatura, falls back to markdownify, normalizes via cleaner. If the response
  is a **PDF** (viewer DOM or `.pdf` path, confirmed by `%PDF-` magic bytes), the
  bytes go to `file_to_markdown` instead — the docling-first document pipeline
  (ADR-017). Trafilatura output passes through `_drop_repeated_run`, which undoes
  the body duplication Trafilatura emits below its 250-char threshold (ADR-020),
  bounded by the size of the *repeat* rather than of the document (ADR-022).
- `parsers/file_parser.py` — engine selection (`WISEAU_PDF_ENGINE`, default
  `docling`): docling-first with automatic fallback to the deterministic parsers —
  PDF via PyMuPDF4LLM (legacy mode) with per-page OCR of scanned pages, DOCX via
  Mammoth + markdownify, images via OCR. Each parser records its own engine
  attribution, so a scanned PDF counts as `ocr`, not `pymupdf`. docling is attempted only when selected
  **and** `WISEAU_DOCLING_BASE` is set; any `DoclingError` logs and falls back.
- `parsers/docling_client.py` *(Phase 6)* — thin **stdlib-`urllib`** HTTP client
  to docling-serve (`WISEAU_DOCLING_BASE`, `WISEAU_DOCLING_TIMEOUT`, and two
  independent credentials: bearer `WISEAU_DOCLING_TOKEN` for a private Space's
  gateway, `X-Api-Key` `WISEAU_DOCLING_API_KEY` for docling-serve's own guard).
  Sends document bytes as multipart, requests `md`, returns raw Markdown (caller
  cleans). Typed errors: `DoclingUnavailable`
  (down/timeout/5xx/empty/non-JSON/401/403/429) vs `DoclingBadDocument` (other
  4xx), both `DoclingError`. Injectable transport for tests; **no new runtime
  dep** (ADR-016/018).
- `parsers/ocr.py` — pluggable OCR engines: default MuPDF-Tesseract (system binary,
  deterministic), opt-in EasyOCR (neural, handwriting; `WISEAU_OCR_ENGINE=easyocr`).
- `parsers/cleaner.py` — deterministic Unicode/whitespace/typography normalizer.
- `Dockerfile` — Python 3.11-slim + system Chromium/chromedriver, non-root user.
- `requirements.txt` — direct dependencies **version-pinned** to verified
  releases; `requirements-dev.txt` — `pytest` + `httpx` for the suite.
- `conftest.py` + `pytest.ini` — put `backend/` on `sys.path`; `tests/` dir holds
  `test_cleaner.py`, `test_file_parser.py` (incl. Phase-6 engine selection),
  `test_docling_client.py`, `test_url_parser.py` (direct-PDF routing over a faked
  driver), `test_api.py`, `test_mcp_server.py`, `test_monitor.py`, `test_ocr.py`,
  `test_ocr_engine.py`, `test_observability.py`, and the opt-in
  `test_browser_live.py` (now also covering a loopback-served PDF URL) — 147 pass
  + 5 skipped in browserless runs.

**CI** (`.github/`)
- `workflows/backend-tests.yml` — two jobs on any `backend/**` change:
  - `test`: installs runtime + dev deps and runs `pytest` (no browser
    provisioned; URL worker is mocked).
  - `docker-build`: builds the image from `backend/Dockerfile`, boots the
    container, asserts `/ping`, checks Chromium/ChromeDriver versions, and runs a
    live `POST /convert/url` on `https://example.com` (real headless-Chrome
    render — GitHub runners have Docker + direct egress). ADR-011.
- `workflows/deploy-frontend.yml` — publishes `frontend/` to GitHub Pages on a
  push to `main` (or manual dispatch), via `upload-pages-artifact`/`deploy-pages`.
  Branch publishing could not serve `frontend/` (it serves a repo root or `/docs`,
  and `/docs` is the documentation). If the `MARKDOWN_API_BASE` repository
  variable is set, the workflow writes it into the *uploaded* `config.js`; the
  committed default stays `localhost`. Never run — Pages is not enabled. ADR-023.

**docling converter** (`docling/`) — *written, never built*
- `Dockerfile` — `FROM ghcr.io/docling-project/docling-serve-cpu:v1.27.0@sha256:a70cd391…`
  (tag + digest pinned; weights baked in, so no boot-time model download).
  Re-homed for HF Spaces: port 7860, UID 1000, writable state under `/tmp`, one
  worker sharing one model copy, page/size caps, `DOCLING_SERVE_MAX_SYNC_WAIT=100`
  so docling's 504 precedes the backend's 120s timeout, demo UI and management
  endpoints off. The API key is **not** baked in — it is a deploy-time secret.
- `README.md` — HF Space card frontmatter + local run, deployment, and the
  ordered live-verification steps (health → fidelity → pause Space → fallback).

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
  deploy-ready* (ADR-011) and both deployments are now prepared in-repo
  (ADR-023), so this is purely account work: create the Spaces, push `backend/`
  and `docling/` to them, set the secrets, set one repository variable, switch
  Pages' source to GitHub Actions. The Space card and the Pages workflow are
  themselves **unrun** — the first deploy is also their first test.
- **MCP server + monitor need a reachable backend.** By design both are HTTP
  clients, so their tools only work when a backend is running at
  `WISEAU_API_BASE`. Verified against a local `uvicorn`/stub; not yet exercised
  against a deployed Space.
- **The docling Space image has never been built.** `docling/Dockerfile` is
  written and its base image digest was verified against the ghcr registry API,
  but no session so far has had both a Docker daemon and the ~4.4 GB of pull
  budget it needs. The *client* half is now proven over a real socket (a loopback
  stub speaking docling-serve's response shape: endpoint, multipart body, both
  credentials, 5xx handling, plus end-to-end fallback and success through the
  running API) — but a stub is not docling. Still unproven: any conversion by
  real docling, and therefore any side-by-side fidelity comparison against the
  PyMuPDF path. Build it before trusting it (ADR-018).
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

*Resolved earlier: live external-URL fetch, Chromium-in-container launch, and the
CI Docker-build gap — all proven (ADR-011). Resolved this session: direct-PDF
URLs, which used to convert to an empty PDF-viewer shell (ADR-017).*

---

## Suggested next actions (see `docs/roadmap.md` for the full backlog)

**Phase 6 has no code left in it, the cross-cutting backlog's code items are done
too, and as of this session both deployments are prepared in-repo (ADR-023).**
Everything remaining in both open tracks needs something this chain of sessions
hasn't had: a Docker daemon with a few GB of pull budget, or external accounts. Once deployed, `GET /metrics` is the fastest way to check the docling
half is actually working (`engines.docling` vs `engines.pymupdf`).

**A. Phase 6 — build and deploy the docling Space (ADR-015/018).**
1. **Build `docling/Dockerfile`** (`docker build -t wiseau-docling docling/`) and
   boot it: `/health` must answer, and a `POST /v1/convert/file` with
   `to_formats=md` must return `document.md_content`. This is the first real test
   of the whole docling half — the client is only proven against a mock.
2. **Deploy it as HF Space #2** (private, 16 GB CPU), set `DOCLING_SERVE_API_KEY`
   as a secret, then set `WISEAU_DOCLING_BASE` + `WISEAU_DOCLING_API_KEY`
   (+ `WISEAU_DOCLING_TOKEN` for the private-Space gateway) on Space #1.
   *Needs an external account.* Steps are in `docling/README.md`.
3. **Live verification** — a table-heavy/scanned government-PDF upload (compare
   docling fidelity against `WISEAU_PDF_ENGINE=pymupdf` on the same file), a
   direct-PDF **URL** through `/convert/url`, and **fallback** (pause Space #2 →
   PyMuPDF still returns, with a `falling back` line in the log).

**B. Phase 5 — deployment (still open; a prerequisite for the live end-to-end
check).** Nothing here needs a commit any more (ADR-023) — only accounts and
settings:
1. Deploy the backend to a Hugging Face Space (free CPU tier): create a **Docker**
   Space and push the *contents of* `backend/` to its repo root (the Space card is
   `backend/README.md`'s frontmatter). The image is proven deploy-ready (ADR-011);
   container listens on `7860`, runs as UID 1000.
2. Set **Settings → Pages → Source** to *GitHub Actions*, set the
   `MARKDOWN_API_BASE` repository variable to the Space URL, and run the **Deploy
   frontend** workflow. (Editing `frontend/config.js` still works, and is the route
   for any non-Pages host.)
3. Confirm the deployed UI talks to the deployed backend end-to-end; re-run the
   MCP + monitor checks against the Space.

---

## Session log

Newest first. One short entry per working session — what changed and what the
next instance should know.

- **2026-07-26 — Operational readiness: the deployment path no longer needs a commit.**
  Asked what actually remains before wiseau is operational, and audited the answer
  instead of restating it. Both open tracks were on record as "needs external
  accounts, not code" — but two of the steps *were* code, and each would have
  failed at the first attempt (**ADR-023**). **(1) Space #1 had no Space card.** A
  Hugging Face Docker Space reads its configuration from YAML frontmatter in the
  Space repo's `README.md`; `docling/README.md` had one, `backend/README.md` did
  not, so the backend Space had no `sdk: docker` and no `app_port`. Added it,
  mirroring `docling/`, plus a deployment section (push the *contents* of
  `backend/` to the Space root; which env vars matter; how to confirm it is live
  via `/ping` + `/metrics`). **(2) GitHub Pages could not have served
  `frontend/`.** Pages' branch publishing serves a repository root or `/docs`
  only, and `/docs` holds this documentation — so the roadmap's "activate GitHub
  Pages" was not a setting that exists. Added
  `.github/workflows/deploy-frontend.yml`: `configure-pages` →
  `upload-pages-artifact` (path `frontend`) → `deploy-pages`, on pushes to `main`
  touching `frontend/**` and on manual dispatch, with `pages: write` +
  `id-token: write` and a non-cancelling `pages` concurrency group (a half-published
  site is worse than a stale one). The workflow also takes the last commit out of
  the deployment path: when the `MARKDOWN_API_BASE` **repository variable** is set
  it rewrites that one assignment in the *uploaded* `config.js`, leaving the
  committed `localhost` default alone — so a public repo needn't carry the
  deployment URL, and local development is untouched. A value that isn't an
  absolute URL fails the job rather than publishing a site that silently calls
  localhost. **Verified** by pulling the step out of the YAML and running it
  against a copy of the real `config.js`: unset → file untouched, set → rewritten
  with the trailing slash stripped, malformed → exit 1; the YAML itself parses.
  **Not verified, and can't be here:** the Space card and the Pages workflow have
  never run — no HF account, no Pages site — so the first deploy is also their
  first test. No backend code changed and the suite was not re-run (its deps
  aren't installed in this sandbox); the test numbers above are the previous
  session's. Docs: ADR-023, roadmap Phase 5 (a new `[x]` for the preparation; the
  four deploy items now spell out the account steps), tech-spec §8,
  `frontend/README.md`, `frontend/config.js` header. **Next instance:** the
  remaining work is now genuinely account-only — build `docling/Dockerfile` when
  you have a Docker daemon, create both Spaces, set `DOCLING_SERVE_API_KEY` and
  the three `WISEAU_DOCLING_*` vars, then Pages source → *GitHub Actions* +
  the `MARKDOWN_API_BASE` variable. Live checks: `docling/README.md` for the
  docling half, `GET /metrics` to confirm docling is really serving conversions.
- **2026-07-26 — Observability, and the Trafilatura duplication bug root-caused.**
  With no Docker daemon and no external accounts available, took the two open
  *code* items from the cross-cutting backlog. **(1) Observability (ADR-019).**
  New `backend/observability.py`: a JSON-lines log formatter (`WISEAU_LOG_FORMAT=text`
  opts out) and a thread-safe in-process metrics registry, surfaced at a new
  `GET /metrics` (API `0.3.0 → 0.4.0`, additive). Every request now emits exactly
  one structured access line — uvicorn's own access log is switched off in the
  Dockerfile CMD so it doesn't duplicate it — carrying a correlation id that is
  also returned as `X-Request-ID`. The registry records request/job timings, the
  semaphore's queue wait and **peak in-flight** count, and process RSS, i.e. the
  numbers `MAX_CONCURRENT_JOBS` should have been tuned against instead of guessed.
  The part that actually motivated it: **engine attribution**. ADR-014 makes a
  docling outage return a *successful* response, so a dead Space is
  indistinguishable from normal operation — every conversion is now counted
  against the engine that produced it (`docling`/`pymupdf`/`mammoth`/`ocr`/
  `trafilatura`/`markdownify`), with docling's successes, fallbacks *by typed
  reason*, and skips (not selected vs not configured) counted apart. Stdlib-only
  and in-process, consistent with ADR-010/016: **no new runtime dependency**,
  nothing to scrape, aggregates only (the endpoint is public, so no URLs,
  filenames, or content). **(2) Trafilatura's duplicated body (ADR-020).** The
  standing note called it a very-small-document quirk; it is more than that. When
  Trafilatura's extraction yields under `MIN_EXTRACTED_SIZE` (250 chars),
  `extract_content` calls `recover_wild_text`, which **extends** the
  already-populated result body with every `<p>`/`<table>` in the document — so
  *any* short page whose content sits in a recognised container comes back with
  its body twice, and because Trafilatura appends comments after the body, the
  repeat isn't always a suffix. `url_parser._drop_repeated_run` now drops the
  longest exact adjacent repeat, guarded to ≤60-block documents and ≥40-char runs
  so a healthy page (and a legitimately repeated "Yes") is untouched. Rejected
  lowering `MIN_EXTRACTED_SIZE` (the same constant steers justext and
  readability-vs-extraction choices elsewhere), deduplicating in `cleaner.py`
  (shared normalizer, must stay non-lossy), and Trafilatura's `deduplicate=True`
  (a process-wide LRU cache — output would depend on what was converted before
  it). **Verified beyond the unit suite.** Fetched a version-matched chromedriver
  and ran the opt-in live-browser suite (**151 pass, 1 skip**). Drove a real
  uvicorn with real headless Chromium against a locally served short notice page:
  the same live DOM yields the body twice unrepaired and once through the
  endpoint. Then exercised the docling half over a **real socket** for the first
  time — a loopback stub speaking docling-serve's response shape returned
  `md_content` and was attributed to `docling` (and the stub confirmed the client
  really sends `POST /v1/convert/file` with `X-Api-Key`), while pointing
  `WISEAU_DOCLING_BASE` at a dead port produced the deterministic result, one
  WARNING naming `DoclingUnavailable`, and `docling.fallbacks: 1` in `/metrics`.
  Codified 41 new tests (**147 pass + 5 skipped** browserless, verified here),
  including two that drive the docling client's *real* `urllib` transport against
  a loopback stub — previously it had only ever talked to an injected transport.
  CI's `docker-build` job now also asserts the short-page repair on the real
  example.com render, the `/metrics` attribution, and that logs are structured
  JSON — all inside the built image. Docs: tech-spec (new §12, §2/§4/§5/§11),
  roadmap (both backlog items → `[x]`, plus the Phase 6 docling-vs-fallback
  metric), decisions (ADR-019, ADR-020), `backend/README.md`. **Next instance:**
  unchanged and entirely external — build `docling/Dockerfile` the moment you have
  a Docker daemon, then deploy both Spaces (Phase 5 + Phase 6) and run the live
  checks in `docling/README.md`. `GET /metrics` is now the quickest way to confirm
  a deployed docling is really serving conversions.
- **2026-07-26 — Phase 6 finished as code: direct-PDF URLs + the docling Space image.**
  Closed the two remaining code items. **(1) Direct-PDF URLs (ADR-017).** A link
  that resolves to a PDF used to render in Chrome's built-in *viewer*, whose DOM is
  a lone `<embed type="application/pdf">` with no text — so `/convert/url` returned
  near-empty Markdown that looked like a success. Now: after the render, if the DOM
  is the viewer *or* the URL path ends in `.pdf`, `browser.fetch_bytes` downloads
  the URL **from inside the already-navigated page** (`fetch()` via
  `execute_async_script`, base64 back over the bridge) so the session's cookies and
  WAF clearance carry over — a second plain HTTP client would be challenged again,
  which would fail exactly the sites that need the stealth browser. Bytes are
  accepted only if they start with `%PDF-`, then handed to `file_to_markdown`, so a
  URL-fetched PDF gets the same docling-first-with-fallback, `clean_markdown()`-ed
  treatment as an upload. A `.pdf` URL that actually serves HTML falls through to
  the normal path; an unmistakable viewer whose bytes are unreachable raises (→ 502
  telling the caller to upload instead) rather than returning the empty shell.
  **Verified live**, not just with fakes: with a version-matched Chromium 141 +
  chromedriver 141, a loopback-served PDF converted to its full text and did so
  reproducibly, while an HTML page on the same server stayed on Trafilatura — and
  the one question fakes couldn't answer (can the PDF-viewer context `fetch()` its
  own URL?) came back yes, revalidating against the browser cache (`304`) rather
  than re-downloading. Codified as 20 faked-driver tests (`tests/test_url_parser.py`)
  plus 2 new opt-in live tests. **(2) The docling Space (ADR-018).**
  `docling/Dockerfile` + `docling/README.md` (HF Space card + deploy steps): the
  upstream `docling-serve-cpu` image, **tag *and* digest pinned**, chosen over a
  from-source build because it bakes the model weights in — a from-source image
  would download them on the first request after every cold start, i.e. precisely
  the request that then blows past `WISEAU_DOCLING_TIMEOUT` and falls back. Re-homed
  for Spaces (port 7860, UID 1000, writable state under `/tmp`, no `chown -R` layer
  that would double a 4.4 GB image), sized for 2 vCPU/16 GB (one worker sharing one
  model copy — a second worker means a second model copy and an OOM kill), and
  `DOCLING_SERVE_MAX_SYNC_WAIT=100` so docling's own 504 lands *before* the
  backend's 120s client timeout, turning a slow conversion into a clean fallback.
  Reading docling-serve's docs also caught a real gap in ADR-015: its API key is an
  **`X-Api-Key`** header (`DOCLING_SERVE_API_KEY`), *not* the bearer token — two
  different layers (platform gateway vs the app). The client now sends both, from
  separate env vars (`WISEAU_DOCLING_TOKEN`, new `WISEAU_DOCLING_API_KEY`), and
  401/403/429 now classify as `DoclingUnavailable`, since a bad credential or a
  rate limit says nothing about the document and shouldn't be logged as "docling
  rejected it". Suite: **106 pass + 5 skipped** (110 with `WISEAU_LIVE_BROWSER=1`
  and a matching driver), verified here. Docs updated: tech-spec §4/§5/§8/§11,
  roadmap Phase 6 + a new backlog note (Trafilatura duplicates the body of
  one-paragraph documents — reproduced calling it directly, so it's the extractor,
  not us), decisions (ADR-017, ADR-018), `backend/README.md`. **Next instance:**
  there is no Phase 6 code left. Build `docling/Dockerfile` the moment you have a
  Docker daemon (nothing downstream of it is proven — the client has only ever
  talked to a mock), then deploy both Spaces and run the live checks in
  `docling/README.md`. Phase 5 deployment is still the other open track.
- **2026-07-26 — Phase 6 backend: docling client + engine selection (built).**
  Turned the Phase 6 plan into code. Added `backend/parsers/docling_client.py`, a
  thin HTTP client to docling-serve, and wired docling-first engine selection with
  automatic fallback into `backend/parsers/file_parser.py`. **Key decision
  (ADR-016):** the roadmap assumed an `httpx` client "already present", but `httpx`
  is only a *dev/MCP* dependency, not a backend runtime one — a hard import would
  make the whole backend fail to load if it were ever absent, against the
  "never hard-depend on docling / degrade to a working result" rule. So the client
  is **stdlib-`urllib`** (mirroring `monitor.py`, ADR-010) with an **injectable
  transport** for testing: the runtime image gains **no** new dependency
  (`requirements.txt` untouched — still no torch, no httpx). Errors are **typed** —
  `DoclingUnavailable` (down/timeout/5xx/empty/non-JSON) vs `DoclingBadDocument`
  (4xx), both `DoclingError` — so `file_parser` catches the base and falls back
  while logging which happened. Engine selection is gated on
  `WISEAU_PDF_ENGINE=docling` (default) **and** `is_configured()`
  (`WISEAU_DOCLING_BASE` set), so with **no base configured (the default)** docling
  is skipped and behaviour is byte-identical to pre-Phase-6 — the existing suite is
  unaffected. Output still flows through `clean_markdown()` (invariant #3) inside
  the unchanged `_job_semaphore` (invariant #4 — no new endpoint). Added **24 tests**
  (mocked transport, no live Space): `tests/test_docling_client.py` (18 — success,
  request shape/endpoint/multipart/`to_formats=md`, auth header, and every failure
  mode's typed error) and 6 engine-selection tests in `test_file_parser.py`
  (docling by default; skipped when unconfigured; `pymupdf` pins the local path;
  fallback on both error kinds; 415 preserved). Full suite **80 pass + 3 skipped**,
  verified here. Updated tech-spec (§4 module table already listed the client; §11
  status banner + fallback detail; §5/§8 env/topology already present), roadmap
  (Phase-6 backend + test tasks → `[x]`), decisions (ADR-016; ADR-014 status →
  implemented). **Next instance:** the rest of Phase 6 — (1) route PDF-typed
  `/convert/url` fetches to docling (needs a `browser.py`/`url_parser.py` change to
  detect a PDF response and return bytes), (2) the `docling/` docling-serve Space,
  (3) live upload + fallback verification. Phase 5 deployment (HF Space for the
  backend + GitHub Pages) is still open and is the prerequisite for the live
  end-to-end check.
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
