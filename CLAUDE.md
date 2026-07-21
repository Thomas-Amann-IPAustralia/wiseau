# CLAUDE.md

Guidance for Claude Code instances working in this repository. **Read this first,
then read [`STATUS.md`](STATUS.md) to see where the project currently stands.**

This project is built by a *series* of Claude Code instances handing work off to
one another. You are one link in that chain. The documents listed below are the
shared memory that keeps the chain on-spec — keep them accurate.

## What this project is

**wiseau** — "Oh hi Mark(down)" — is the **Universal Markdown Ingestion Engine**:
a deterministic, self-hosted microservice that converts web URLs, PDFs, and DOCX
documents into clean, structured Markdown. It serves two consumers from one
contract: humans (via a static web UI) and LLM/agents (via the HTTP API +
OpenAPI schema).

The design values, in priority order:

1. **Deterministic** — identical input yields identical Markdown. Favour
   algorithmic extraction (Trafilatura) over per-site CSS selectors.
2. **Decoupled** — static frontend and containerized backend talk only over HTTP.
3. **Memory-aware** — headless browsing and PDF extraction are heavy; the box is
   sized and rate-limited to avoid OOM under concurrent load.
4. **Agent-native** — the API is a first-class integration surface.
5. **Open but protected** — the API is public; fair-use rate limiting and a
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
├── backend/                # FastAPI microservice (Hugging Face Spaces target)
│   ├── main.py             # routing, CORS, rate limiting, concurrency ceiling
│   ├── mcp_server.py       # MCP tool surface (thin HTTP adapter over the API)
│   ├── parsers/            # deterministic extraction pipeline
│   │   ├── browser.py      # Selenium-stealth headless Chrome
│   │   ├── url_parser.py   # Trafilatura extraction (+ markdownify fallback)
│   │   ├── file_parser.py  # PyMuPDF4LLM (PDF) + Mammoth (DOCX)
│   │   └── cleaner.py      # regex/Unicode normalization
│   ├── Dockerfile          # version-locks Chromium + Python
│   └── requirements.txt
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

> **Note:** There is no automated test suite or CI yet. Adding one is an open
> roadmap item — see `docs/roadmap.md`. Until it exists, verify changes with the
> manual smoke checks above and say so honestly in `STATUS.md`.

## Working rules for this repo

- **Determinism is the product.** Before adding any per-site logic, timestamp,
  random value, or ordering that depends on dict iteration, ask whether the same
  input still produces the same Markdown. If not, reconsider.
- **All extracted output flows through `cleaner.clean_markdown()`.** Don't return
  Markdown from a new parser without running it through the shared normalizer.
- **The API contract is shared by humans and agents.** Both consume the same
  `MarkdownResponse` JSON. Don't fork the contract per consumer. Contract changes
  go in `tech-spec.md` and bump the API `version` in `main.py`.
- **Respect the fair-use guards.** Heavy work belongs inside the `_job_semaphore`
  and behind the rate limiter. Don't add an endpoint that bypasses them.
- **Keep the layers decoupled.** The frontend must reach the backend only over
  HTTP via `MARKDOWN_API_BASE`. No build-time coupling.
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
