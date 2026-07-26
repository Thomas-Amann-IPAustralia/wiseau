# CLAUDE.md

Guidance for Claude Code instances working in this repository. **Read this first,
then read [`STATUS.md`](STATUS.md) to see where the project currently stands.**

This project is built by a *series* of Claude Code instances handing work off to
one another. You are one link in that chain. The documents listed below are the
shared memory that keeps the chain on-spec — keep them accurate.

## What this project is

**wiseau** — "Oh hi Mark(down)" — is the **Universal Markdown Ingestion Engine**:
a self-hosted service that converts web URLs, PDFs, DOCX, and images into clean,
structured Markdown. It serves two consumers from one contract: humans (via a
static web UI) and LLM/agents (via the HTTP API + OpenAPI schema).

> **Direction (2026-07-24).** The project is evolving to prioritise **fidelity** —
> the most faithful Markdown of real-world documents (complex, multi-column, and
> scanned government PDFs) — **over strict determinism**. **docling** becomes the
> *default* document parser, running as an internal microservice, with the
> original PyMuPDF/Mammoth path kept as an **automatic fallback**. This is the
> Phase 6 work; the decisions are on record in ADR-013/014/015 and the task list
> is in `roadmap.md` Phase 6. The values below already reflect this.

The design values, in priority order:

1. **Faithful first** — produce the most accurate Markdown of the source, even
   when that means a *stochastic* ML extractor (docling). Determinism is no longer
   the top goal: the paths that *are* deterministic (the normalizer, the
   PyMuPDF/Mammoth fallback, Trafilatura URL extraction) stay so, but the default
   docling path may vary run-to-run **by design** (ADR-013 — do not "fix" it).
   Still favour algorithmic/model extraction over per-site CSS selectors.
2. **Resilient** — docling on the free tier is slow and cold-starts, so document
   conversion is docling-first with an **automatic fallback** to the deterministic
   parsers: the service degrades to a working result rather than failing (ADR-014).
3. **Decoupled** — static frontend, backend, and the docling converter talk only
   over HTTP. The backend's only knowledge of docling is `WISEAU_DOCLING_BASE`.
4. **Memory-aware** — headless browsing and ML extraction are heavy; each service
   is sized (16 GB HF Space) and rate-limited to avoid OOM. docling and the browser
   live in *separate* Spaces so neither starves the other.
5. **Agent-native** — the API is a first-class integration surface.
6. **Open but protected** — the API is public; fair-use rate limiting and a
   concurrency ceiling guard it, not origin locks.

## The document map

| Document | Purpose | Update cadence |
| -------- | ------- | -------------- |
| [`CLAUDE.md`](CLAUDE.md) | This file — entry point & working rules. | Rarely; only when conventions change. |
| [`STATUS.md`](STATUS.md) | Live snapshot: what's done, in progress, next. | **Every session that changes anything.** |
| [`docs/project-brief.md`](docs/project-brief.md) | The vision / "why". Stable north star. | Rarely; scope changes only. |
| [`docs/tech-spec.md`](docs/tech-spec.md) | The detailed "how": API contracts, modules, config, errors. | When behaviour/contract changes. |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased task backlog with checkboxes. | As tasks start/finish. |
| [`docs/decisions.md`](docs/decisions.md) | ADR log — decisions and their rationale. | When a non-trivial decision is made. |
| [`docs/mcp.md`](docs/mcp.md) | Agent integration: MCP tools + function-calling surface. | When the agent-facing surface changes. |
| [`docs/agent-workflow.md`](docs/agent-workflow.md) | How instances pick up, execute, and hand off work. | Rarely. |

If you're unsure where a piece of information belongs: *facts about current
state* → `STATUS.md`; *how the system should behave* → `tech-spec.md`; *why we
chose something* → `decisions.md`; *what's left to do* → `roadmap.md`.

## Repository layout

```
wiseau/
├── CLAUDE.md               # you are here
├── STATUS.md               # live project state
├── README.md               # public-facing one-liner
├── docs/                   # all project documentation (see map above)
├── backend/                # FastAPI microservice — HF Space #1 (backend + Chrome)
│   ├── main.py             # routing, CORS, rate limiting, concurrency ceiling
│   ├── mcp_server.py       # MCP tool surface (thin HTTP adapter over the API)
│   ├── parsers/            # extraction pipeline
│   │   ├── browser.py      # Selenium-stealth headless Chrome (render + in-session download)
│   │   ├── url_parser.py   # Trafilatura extraction; direct-PDF URLs -> file_parser
│   │   ├── file_parser.py  # engine select: docling-first, PyMuPDF/Mammoth fallback
│   │   ├── docling_client.py  # [Phase 6] thin HTTP client to docling-serve
│   │   ├── ocr.py          # pluggable OCR engines (Tesseract / EasyOCR)
│   │   └── cleaner.py      # regex/Unicode normalization (always runs)
│   ├── Dockerfile          # version-locks Chromium + Python
│   └── requirements.txt
├── docling/                # [Phase 6] docling-serve converter — HF Space #2
│   ├── Dockerfile          # upstream docling-serve-cpu image, tag+digest pinned
│   └── README.md           # HF Space card + deployment/verification steps
└── frontend/               # static UI (GitHub Pages target)
    ├── index.html
    ├── style.css
    ├── app.js              # state + fetch calls
    └── config.js           # per-deployment: MARKDOWN_API_BASE
```

## How to build, run, and check

**Backend (local):**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 7860
# Chromium + chromedriver must be on PATH, or set CHROME_BIN / CHROMEDRIVER_PATH.
```

**Backend (Docker — matches production):**
```bash
cd backend
docker build -t markdown-engine .
docker run -p 7860:7860 markdown-engine
```

**Frontend (local):**
```bash
cd frontend
python -m http.server 8000   # then open http://localhost:8000
```

**Quick manual smoke checks:**
```bash
curl localhost:7860/ping
curl -X POST localhost:7860/convert/url \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com"}'
open localhost:7860/docs          # interactive OpenAPI docs
```

**Run the tests:**
```bash
cd backend
pip install -r requirements-dev.txt
pytest                          # browserless suite (URL worker mocked)
WISEAU_LIVE_BROWSER=1 pytest    # also runs the opt-in live-Chromium tests
```

> **Note:** CI (`.github/workflows/backend-tests.yml`) runs the browserless suite
> plus a Docker build that renders a live URL in-container. Phase 6 (docling) tests
> run against a **mocked** docling-serve transport, so they need no live docling
> Space — see `docs/roadmap.md`. Still verify what you change and record it
> honestly in `STATUS.md`.

## Working rules for this repo

- **Fidelity is the product; determinism where it's free (ADR-013).** The goal is
  the most faithful Markdown of the source. The default docling path is stochastic
  **on purpose** — do not "fix" run-to-run variation there. But keep the paths that
  *are* deterministic deterministic: never add a timestamp, random value, or
  dict-iteration-ordered output to the normalizer, the PyMuPDF/Mammoth fallback, or
  the URL path. Still prefer algorithmic/model extraction over per-site selectors.
- **Document conversion is docling-first with automatic fallback (ADR-014).** A new
  document parser slots into the engine-selection layer and must fall back cleanly
  when docling is unavailable — never make the service hard-depend on the docling
  Space.
- **All extracted output flows through `cleaner.clean_markdown()`.** Don't return
  Markdown from any parser (docling included) without running it through the shared
  normalizer.
- **The API contract is shared by humans and agents.** Both consume the same
  `MarkdownResponse` JSON. Don't fork the contract per consumer. Contract changes
  go in `tech-spec.md` and bump the API `version` in `main.py`.
- **Respect the fair-use guards.** Heavy work belongs inside the `_job_semaphore`
  and behind the rate limiter. Don't add an endpoint that bypasses them.
- **Keep the layers decoupled.** The frontend reaches the backend only over HTTP
  via `MARKDOWN_API_BASE`; the backend reaches docling only over HTTP via
  `WISEAU_DOCLING_BASE`. No build-time coupling; docling-serve is internal (never
  called by the public directly).
- **Match the surrounding style.** Python: type hints, module docstrings, `from
  __future__ import annotations`. JS: vanilla, no framework, no build step.
- **Leave the campsite documented.** End every working session by updating
  `STATUS.md` (and `roadmap.md` checkboxes / `decisions.md` if relevant).

## Git & branch discipline

- Development branch for documentation/scaffolding work is set per task; do not
  push to `main` without explicit permission.
- Commit in coherent, self-describing units. Reference the phase/task where it
  helps (e.g. "Phase 4: add MCP server wrapping /convert endpoints").
- Never commit secrets, `.env`, or `.venv/` (see `.gitignore`).
