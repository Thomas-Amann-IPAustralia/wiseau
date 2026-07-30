# Project Brief: Universal Markdown Ingestion Engine

> Oh hi Mark(down) — an engine that converts web pages and documents into clean, structured Markdown.

> **Scope evolution (2026-07-24).** The project treats **fidelity as the
> product**: the goal is the most faithful Markdown of real-world documents
> (complex, multi-column, and scanned government PDFs). **docling** is available
> as a high-fidelity document parser, running as an internal microservice (a
> second Hugging Face Space), alongside the original PyMuPDF/Mammoth path — which
> is also docling's **automatic fallback**. This is Phase 6 (§3); rationale is in
> ADR-013 (fidelity over determinism), ADR-014 (the docling engine + fallback),
> and ADR-015 (the two-Space topology). The sections below are amended to match;
> where they still say "deterministic", read it as "faithful, and deterministic on
> the paths where that is free."
>
> **Amendment (2026-07-30, ADR-027).** docling is no longer the *default* engine:
> the fast local parser is, because it converts the ordinary document in about a
> second where free-tier docling costs tens of seconds to minutes. docling is
> chosen deliberately — per deployment (`WISEAU_PDF_ENGINE=docling`) or per
> request (`engine="docling"`) — and its fallback is unchanged. Speed is the
> default; fidelity is one click or one field away.

## 1. Overview

The Universal Markdown Ingestion Engine is a deterministic, self-hosted microservice
that converts arbitrary web URLs, PDFs, and DOCX documents into clean, structured
Markdown. It is designed to serve as a foundational data-ingestion layer for both
human-facing interfaces and automated background pipelines.

The system targets the rigorous demands of tracking authoritative IP sources,
legislative instruments, and terms-of-service updates — workloads where extraction
must be reliable, repeatable, and resistant to performance bottlenecks.

Two classes of consumer drive the design:

- **Humans** interact through a static web UI (paste a URL, drop a file, read the
  rendered Markdown).
- **LLMs and agents** interact through the API, exposed natively over HTTP and
  described by an OpenAPI schema so it can be wired into MCP servers and
  function-calling frameworks.

### Design principles

- **Faithful first (amended — ADR-013/027).** Produce the most accurate Markdown of
  the source, and make a stochastic ML extractor (docling) available when that is
  what accuracy takes. Favour algorithmic/model extraction over fragile,
  layout-specific CSS selectors. The default paths are deterministic (the
  normalizer, the PyMuPDF/Mammoth parsers, Trafilatura URL extraction) and stay so;
  the opt-in docling path may vary run-to-run by design.
- **Fast by default (added — ADR-027).** The engine that runs when nobody chose one
  is the one that answers in about a second. A minute of ML inference is a
  deliberate choice, not the price of every conversion.
- **Resilient (added — ADR-014).** When docling *is* selected, conversion falls back
  automatically to the deterministic parsers, so a cold/slow/down docling service
  degrades to a working result instead of failing.
- **Decoupled layers.** A static frontend and a containerized backend communicate only
  over HTTP, keeping the UI free to evolve independently of the engine.
- **Memory-aware.** Headless browsing and PDF extraction are memory-heavy; the compute
  tier is sized to avoid out-of-memory crashes under concurrent load.
- **Agent-native.** The API is a first-class integration surface, not an afterthought
  bolted onto a UI.
- **Open but protected.** The API is intentionally public — anyone may call it, paying
  the free compute forward. Access is guarded not by origin locks but by fair-use rate
  limiting and a concurrency ceiling that keeps shared compute healthy for everyone.

## 2. System Architecture

The system is split into two independent layers that communicate over standard HTTP,
optimized for memory-heavy operations.

| Component    | Technology                    | Responsibility                                                                 | Hosting                        |
| ------------ | ----------------------------- | ------------------------------------------------------------------------------ | ------------------------------ |
| Frontend       | HTML5 / CSS3 / JavaScript   | Custom UI/UX, user input collection, state management, and Markdown rendering. | GitHub Pages (static, free)    |
| Backend API    | FastAPI (Python)            | Headless browser execution (WAF-bypass fetch), engine selection, fallback parsing, and the shared contract + guards. | Hugging Face Space #1 (Docker) |
| docling-serve  | docling (PyTorch)           | *(Phase 6)* Opt-in high-fidelity document→Markdown converter; internal, called only by the backend. | Hugging Face Space #2 (Docker) |
| Compute        | 16 GB RAM, 2 vCPU per Space | Prevents OOM during heavy PDF/ML extraction and concurrent web scraping; browser and docling live in separate Spaces so neither starves the other. | Hugging Face free tier         |

### Request flow

```
Human (browser) ─┐
                 ├─► HTTPS ─► FastAPI backend ─► extraction/parsing ─► clean Markdown (JSON)
LLM / MCP agent ─┘
```

The backend returns JSON payloads containing the extracted Markdown, so both the UI and
automated agents consume an identical contract.

## 3. Milestone Plan

### Phase 1 — Backend API Development (Python Engine)

Build and test the core parsing microservice locally.

- **Environment setup.** Initialize a Python virtual environment and a `requirements.txt`
  containing `fastapi`, `uvicorn`, `pymupdf4llm`, `mammoth`, `trafilatura`, `selenium`,
  `selenium-stealth`, `markdownify`, and `slowapi` (rate limiting).
- **Endpoints.** Construct the primary FastAPI routes — `GET /ping`, `POST /convert/url`,
  and `POST /convert/file` — to handle status checks and return JSON payloads containing
  clean Markdown.
- **CORS.** Configure `CORSMiddleware` with a permissive origin policy so the public
  API is reachable from any browser client (the hosted UI, forks, and third-party
  frontends), rather than locking to a single origin.
- **Rate limiting & fair use.** Add a per-IP rate limiter (e.g. `slowapi`, backed by an
  in-memory or lightweight store) and a global concurrency ceiling so shared free
  compute stays healthy. Requests over the limit receive `429 Too Many Requests`;
  requests over the concurrency cap queue rather than overwhelming the box.

### Phase 2 — Algorithmic Scraper Integration

Extract the core logic from the batch-processing script and upgrade it for live,
universal API execution.

- **Headless browser configuration.** Implement `initialize_driver()` using
  `selenium-stealth` and robust Chrome arguments (`--no-sandbox`,
  `--disable-dev-shm-usage`) to reliably render JavaScript and bypass basic bot
  protections.
- **Algorithmic extraction.** Integrate `trafilatura.extract()` in place of fragile CSS
  selectors, ensuring deterministic extraction of primary semantic content across any
  page layout.
- **Markdown polish engine.** Port the custom regex post-processing to strip residual
  noise, normalize characters, and format ATX headings for uniform output.

### Phase 3 — Static Frontend Development (JS/CSS)

Build a fully custom, responsive UI with robust state management.

- **Structure & style.** Create a responsive interface (`index.html`, `style.css`) with
  input containers, file-upload drop zones, and an output display pane.
- **Application logic.** Write `app.js` to manage UI state, using `fetch` to send URLs
  or binary file objects to the FastAPI endpoints.
- **Status indication.** Add a visual server-status badge that pings the backend on page
  load to confirm readiness.

### Phase 4 — AI & Agentic Integration

Structure the API so external systems can leverage the extraction engine natively.

- **OpenAPI specification.** Leverage FastAPI's native `openapi.json` generation to
  expose the microservice schema to external LLMs and agentic frameworks.
- **MCP surface.** Provide an MCP server that wraps the endpoints so agents can call
  extraction as a tool.
- **Autonomous ingestion.** Enable agents to query the endpoints via standard function
  calling, supporting automated diff-checking and continuous background monitoring.

### Phase 5 — Containerization & Deployment

Automate the deployment pipeline and provision the cloud hardware.

- **Dockerized backend.** Package the FastAPI app with a custom `Dockerfile` on Hugging
  Face Spaces to version-lock Chromium, Python, and system-level dependencies.
- **Hardware allocation.** Deploy to the Hugging Face free CPU tier so high-memory
  operations execute reliably without container crashes.
- **Cold-start mitigation.** Rely on the generous 48-hour inactivity timeout of Hugging
  Face Spaces to keep the API persistently warm for daily background checks.
- **Frontend deployment.** Push the static UI to GitHub and activate GitHub Pages on the
  main branch.

### Phase 6 — Higher-fidelity extraction via docling (added 2026-07-24)

Offer docling as a selectable document parser for faithful Markdown of complex and
scanned documents, without sacrificing availability on the free tier. *(Amended
2026-07-30, ADR-027: docling was originally adopted as the **default** parser;
the default is now the fast local parser and docling is chosen per deployment or
per request, because the free-tier cost of docling is minutes and most documents
do not need it.)*

- **docling client + engine selection.** Add `parsers/docling_client.py` (a thin
  HTTP client to docling-serve) and an engine-selection layer in `file_parser.py`
  (`WISEAU_PDF_ENGINE`, default `pymupdf`) that runs docling when selected, with
  **automatic fallback** to PyMuPDF4LLM/Mammoth. All output still passes through
  `clean_markdown()`.
- **docling-serve Space.** Package docling-serve in its own `Dockerfile`
  (pinned + model weights pre-downloaded at build), deployed as a second Hugging
  Face Space and called only by the backend (guarded by `WISEAU_DOCLING_TOKEN`).
- **Verify.** Unit-test the client/selection against a mocked transport; then
  verify live — a table-heavy/scanned upload converts via docling, and the service
  falls back to PyMuPDF when the docling Space is stopped.

See ADR-013/014/015, `tech-spec.md` §11, and `roadmap.md` Phase 6 for the full
detail.

## 4. API Surface

| Method | Path            | Purpose                                                    |
| ------ | --------------- | ---------------------------------------------------------- |
| GET    | `/ping`         | Liveness/readiness check for the status badge and monitors.|
| POST   | `/convert/url`  | Fetch, render, and extract a URL into Markdown.            |
| POST   | `/convert/file` | Parse an uploaded PDF or DOCX into Markdown.               |

All conversion endpoints return a JSON payload containing the extracted Markdown, so the
UI and automated agents share a single contract. The auto-generated `openapi.json`
serves as the machine-readable description for LLM and MCP integration.

## 5. Directory Structure

```
markdown-converter/
├── frontend/                 # Static site hosted on GitHub Pages
│   ├── index.html
│   ├── style.css
│   └── app.js                # State management & API fetch calls
└── backend/                  # Containerized microservice hosted on Hugging Face
    ├── Dockerfile            # Installs Chromium, Python, and system libs
    ├── requirements.txt
    ├── main.py               # FastAPI routing and CORS configuration
    └── parsers/
        ├── __init__.py
        ├── browser.py        # Selenium Stealth initialization
        ├── url_parser.py     # Trafilatura + Markdownify extraction
        ├── file_parser.py    # PyMuPDF4LLM & Mammoth implementations
        └── cleaner.py        # Regex post-processing and text normalization
```

## 6. Technology Rationale

| Choice              | Why                                                                                     |
| ------------------- | --------------------------------------------------------------------------------------- |
| FastAPI             | Async, minimal boilerplate, and native OpenAPI generation for agent integration.        |
| Trafilatura         | Algorithmic main-content extraction that is robust across layouts — the determinism core.|
| Selenium + Stealth  | Renders JavaScript-heavy pages and clears basic bot protection before extraction.        |
| docling             | *(Phase 6, opt-in — ADR-027)* ML layout + table-structure + OCR pipeline; the most faithful engine for complex/scanned documents. Runs as an internal microservice. |
| PyMuPDF4LLM         | Fast, deterministic PDF-to-Markdown; the **default** engine, and docling's automatic fallback. |
| Mammoth             | Clean DOCX-to-Markdown conversion that preserves semantic structure.                     |
| Markdownify         | Deterministic HTML-to-Markdown fallback for the polish stage.                            |
| SlowAPI             | Per-IP rate limiting on FastAPI to keep the open, shared compute fair and healthy.       |
| GitHub Pages        | Free, zero-maintenance static hosting for the decoupled frontend.                       |
| Hugging Face Spaces | Free Docker hosting with high memory and a long inactivity timeout for warm background use.|

## 7. Open Questions & Risks

- **Bot protection.** `selenium-stealth` clears basic protections; sites behind advanced
  anti-bot systems (e.g. aggressive CAPTCHA or fingerprinting) may still require
  per-source handling or are out of scope.
- **Concurrency limits.** Free-tier compute caps how many headless browser sessions run
  in parallel. A per-IP rate limiter and a global concurrency ceiling (with queuing)
  keep the shared box healthy; the exact thresholds need tuning against observed memory
  use per job type (a browser render costs far more than a DOCX parse).
- **Abuse of open access.** A public, unauthenticated API invites scraping-as-a-service
  abuse. Rate limiting is the first line of defence; if it proves insufficient, options
  include per-IP daily quotas, blocklists, or an optional API key for higher tiers —
  without closing off casual free use.
- **Determinism boundaries.** Extraction is deterministic given identical input, but live
  pages change; monitoring pipelines must account for legitimate content drift when
  diff-checking.
- **Single-instance ceiling.** A free-tier Space is one container, so there is no true
  horizontal load balancing — throughput is bounded by that instance. The rate limiter
  and concurrency queue smooth load rather than scale it; sustained demand beyond one
  box would require a paid multi-replica tier, which is out of scope for the free,
  pay-it-forward model.
