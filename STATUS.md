# STATUS

> **Live snapshot of where the project stands.** This is the first thing to read
> after [`CLAUDE.md`](CLAUDE.md) and the first thing to update before you end a
> session. Keep it honest — "scaffolded but untested" is more useful than a
> green checkmark that lies.

**Last updated:** 2026-07-21
**Updated by:** Claude Code (Phase 3 frontend end-to-end verification session)
**Overall phase:** Phases 1–3 verified end-to-end. Backend API, the live render
pipeline, and now the **static frontend driven against a running backend** are all
proven. Phase 4 (MCP/agentic) untouched; Phase 5 (Docker build / deploy) open —
Docker-image build and live *external*-URL fetch still need an environment with a
Docker daemon / direct egress.

---

## At a glance

| Area | State | Notes |
| ---- | ----- | ----- |
| Backend API (Phase 1) | 🟢 Verified (browser-free) | Routes, CORS, rate limiting, concurrency ceiling written; app imports cleanly; `/ping`, `/convert/file` (real PDF), `/convert/url` (mocked driver) verified via `TestClient`. Live URL render still unproven. |
| Scraper / extraction (Phase 2) | 🟢 Verified (host) | Live headless-Chrome render → Trafilatura → cleaner proven end-to-end and codified as an opt-in test; DOCX-body path now covered. Only fetching arbitrary **external** URLs is unproven here (sandbox egress proxy; works with direct egress). |
| Frontend UI (Phase 3) | 🟢 Verified | Full static UI driven end-to-end with headless Chromium against a live `uvicorn` backend: status badge, URL + PDF + DOCX conversion, copy/download, and error states all confirmed (18/18 UI checks). See ADR-008. |
| AI / MCP integration (Phase 4) | 🔴 Not started | OpenAPI auto-generated (presence asserted in tests); no MCP server or agent tooling yet. |
| Containerization & deploy (Phase 5) | 🔴 Not deployed | `Dockerfile` written; nothing deployed to Hugging Face or GitHub Pages. |
| Automated tests | 🟢 Passing | 31 pass + 2 skipped in default (browserless) runs. With a version-matched Chromium+chromedriver and `WISEAU_LIVE_BROWSER=1`, the 2 opt-in live-browser tests also run → **33/33** (confirmed this session). Covers `cleaner`/PDF/**DOCX**/validation plus the live render→extract→clean pipeline. |
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

- **Live *external*-URL fetch unproven in this sandbox.** The render pipeline is
  proven (headless Chrome launches, renders a `data:` page, Trafilatura + cleaner
  emit Markdown), but fetching arbitrary internet URLs is blocked here by the
  sandbox's authenticated egress proxy (`net::ERR_CONNECTION_RESET`). Not a code
  issue — needs an environment with direct egress (the Docker image / a Space).
- **Chromium-in-container unconfirmed.** Launch is proven on the host; building
  the actual Docker image and launching Chromium inside it is still open.
- **No deployment** — no live Hugging Face Space, no GitHub Pages activation,
  so `MARKDOWN_API_BASE` still points at localhost.
- **Phase 4 (MCP/agentic) untouched** beyond FastAPI's auto-generated schema.
- CI runs tests but **does not yet build the Docker image**.

---

## Suggested next actions (see `docs/roadmap.md` for the full backlog)

1. **Build the Docker image and confirm Chromium launches inside the container**,
   then verify a real *external* URL renders end-to-end (needs a Docker daemon +
   direct egress — neither is available in this sandbox, so this is the natural
   place to prove it). This is the last piece of Phase 2 / start of Phase 5.
2. **Phase 4 — MCP integration.** Wrap `/convert/url` and `/convert/file` as MCP
   tools over the same `MarkdownResponse` contract, and document the
   function-calling/MCP surface for external agents. This is pure code and fully
   buildable here — the highest-value work now that Phases 1–3 are proven.
3. **Extend CI to build the Docker image** (the last cross-cutting test gap) —
   pairs with item 1; needs a Docker-capable runner.
4. **Then** proceed to deployment (Phase 5): Hugging Face Space + GitHub Pages,
   and point `frontend/config.js` at the live Space.

*Done this session (previously item 2): the shipped frontend is verified
end-to-end against a running backend — status badge, URL/PDF/DOCX conversion,
copy/download, and error states all confirmed by driving the real UI. See the
session log and ADR-008.*

---

## Session log

Newest first. One short entry per working session — what changed and what the
next instance should know.

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
