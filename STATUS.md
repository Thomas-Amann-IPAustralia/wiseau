# STATUS

> **Live snapshot of where the project stands.** This is the first thing to read
> after [`CLAUDE.md`](CLAUDE.md) and the first thing to update before you end a
> session. Keep it honest — "scaffolded but untested" is more useful than a
> green checkmark that lies.

**Last updated:** 2026-07-20
**Updated by:** Claude Code (test-suite & dependency-pinning session)
**Overall phase:** Phase 1 backend verified (browser-free); Phases 2–3 partially
proven; Phases 4–5 open. Test suite + CI + pinned deps now in place.

---

## At a glance

| Area | State | Notes |
| ---- | ----- | ----- |
| Backend API (Phase 1) | 🟢 Verified (browser-free) | Routes, CORS, rate limiting, concurrency ceiling written; app imports cleanly; `/ping`, `/convert/file` (real PDF), `/convert/url` (mocked driver) verified via `TestClient`. Live URL render still unproven. |
| Scraper / extraction (Phase 2) | 🟡 Partly proven | `cleaner` + PDF path tested end-to-end. Trafilatura/Selenium live render and DOCX-body path not yet exercised (need Chromium/Docker). |
| Frontend UI (Phase 3) | 🟡 Code complete, untested | Full static UI written. Not exercised against a running backend. |
| AI / MCP integration (Phase 4) | 🔴 Not started | OpenAPI auto-generated (presence asserted in tests); no MCP server or agent tooling yet. |
| Containerization & deploy (Phase 5) | 🔴 Not deployed | `Dockerfile` written; nothing deployed to Hugging Face or GitHub Pages. |
| Automated tests | 🟢 Passing (browser-free) | 28 tests: `cleaner`, file dispatch + PDF round-trip, API validation/error codes. Live-browser + DOCX-body paths still uncovered. |
| CI/CD | 🟡 Tests wired | `.github/workflows/backend-tests.yml` runs `pytest` on `backend/**`. Docker-build step still open. |
| Documentation | 🟢 Established | Brief, tech spec, roadmap, decisions, agent workflow, this file. |

Legend: 🟢 done & verified · 🟡 written but not verified · 🔴 not started/absent

---

## What exists right now

**Backend** (`backend/`)
- `main.py` — `GET /ping`, `POST /convert/url`, `POST /convert/file`; permissive
  CORS; `slowapi` per-IP limits; `asyncio.Semaphore` concurrency ceiling; upload
  size cap. Heavy work offloaded via `asyncio.to_thread`.
- `parsers/browser.py` — `initialize_driver()` with selenium-stealth + hardened
  Chrome args, env-configurable Chrome/driver paths.
- `parsers/url_parser.py` — renders with headless Chrome, extracts with
  Trafilatura, falls back to markdownify, normalizes via cleaner.
- `parsers/file_parser.py` — PDF via PyMuPDF4LLM, DOCX via Mammoth + markdownify.
- `parsers/cleaner.py` — deterministic Unicode/whitespace/typography normalizer.
- `Dockerfile` — Python 3.11-slim + system Chromium/chromedriver, non-root user.
- `requirements.txt` — direct dependencies **version-pinned** to verified
  releases; `requirements-dev.txt` — `pytest` + `httpx` for the suite.
- `conftest.py` + `pytest.ini` — put `backend/` on `sys.path`; `tests/` dir holds
  `test_cleaner.py`, `test_file_parser.py`, `test_api.py` (28 tests, all green).

**CI** (`.github/`)
- `workflows/backend-tests.yml` — installs runtime + dev deps and runs `pytest`
  on any `backend/**` change (no browser provisioned; URL worker is mocked).

**Frontend** (`frontend/`)
- `index.html`, `style.css`, `app.js` — tabbed URL/file UI, drop zone, status
  badge, copy/download of output, light/dark aware.
- `config.js` — single per-deployment knob `MARKDOWN_API_BASE` (default
  `http://localhost:7860`).

**Docs** (`docs/` + root) — see the document map in `CLAUDE.md`.

---

## Known gaps / not yet proven

- **Live browser render unproven.** No confirmation Chromium launches (locally or
  in the container) or that a real URL extracts end-to-end. The `/convert/url`
  test mocks the browser worker; the live path needs Docker to verify.
- **DOCX-body extraction untested.** File dispatch and the PDF path are covered;
  a real `.docx` round-trip is not (no `python-docx` at hand to synthesize one).
- **No deployment** — no live Hugging Face Space, no GitHub Pages activation,
  so `MARKDOWN_API_BASE` still points at localhost.
- **Phase 4 (MCP/agentic) untouched** beyond FastAPI's auto-generated schema.
- CI runs tests but **does not yet build the Docker image**.

---

## Suggested next actions (see `docs/roadmap.md` for the full backlog)

1. **Prove the live browser path.** Build the Docker image and confirm Chromium
   launches and a real URL renders + extracts end-to-end. This is the biggest
   remaining unknown and unblocks flipping Phase 2 fully green.
2. **Load the frontend against a running backend** (Phase 3): confirm URL + file
   conversion, copy/download, and error states render sensibly.
3. **Extend CI to build the Docker image**, and add a real DOCX-body test.
4. **Then** proceed to deployment (Phase 5) and MCP integration (Phase 4).

*Done this session (previously items 2–3): browser-free test suite (28 tests) and
dependency pinning are complete — see the session log.*

---

## Session log

Newest first. One short entry per working session — what changed and what the
next instance should know.

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
