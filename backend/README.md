---
title: wiseau
emoji: 📝
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Backend — Markdown Ingestion Engine

FastAPI microservice that converts URLs, PDFs, DOCX documents, and images into
clean Markdown. Scanned and handwritten PDFs (and image uploads) are OCR'd
automatically. See [`../docs/project-brief.md`](../docs/project-brief.md) for the
full design.

## Endpoints

| Method | Path            | Purpose                                          |
| ------ | --------------- | ------------------------------------------------ |
| GET    | `/ping`         | Liveness/readiness check (rate-limit exempt).    |
| POST   | `/convert/url`  | `{ "url": "...", "engine": "auto" }` → Markdown JSON. |
| POST   | `/convert/file` | multipart `file` (PDF/DOCX/image) + optional `engine` → Markdown JSON.|
| GET    | `/metrics`      | Per-process operational counters (see below).    |

`/convert/url` refuses a URL that resolves to a loopback/private/link-local
address with a **400** — the endpoint is public and the fetch happens inside the
container, so an unguarded renderer is an SSRF primitive. Set
`WISEAU_ALLOW_PRIVATE_URLS=1` on a self-hosted deployment that converts its own
intranet. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §13 and ADR-021.

Both convert endpoints take an optional **`engine`**: `pymupdf` (fast,
deterministic — what `WISEAU_PDF_ENGINE` defaults to), `docling` (highest
fidelity, far slower on free CPU), or `auto` (the parameter's default — use this
deployment's `WISEAU_PDF_ENGINE`). An unknown value is a **400**. Requesting
`docling` does not disable the automatic fallback, and `GET /ping` lists both the
names this build accepts and the deployment's `default_engine`. See ADR-025 and
ADR-027.

Interactive docs and the machine-readable schema for LLM/MCP integration are
served at `/docs` and `/openapi.json`.

`/convert/url` also handles **direct-PDF links** (a `.pdf` URL, or any URL that
serves a PDF): the bytes are downloaded through the browser's own session and run
through the document pipeline below, rather than extracting Chrome's empty PDF
viewer. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §4 and ADR-017.

## High-fidelity conversion (docling)

Document conversion runs the **fast local parsers by default** (ADR-027) — about
a second per document, and byte-reproducible. **docling** is the high-fidelity
alternative: with a docling-serve service configured *and* selected, PDFs/DOCX/
images are converted there for markedly more faithful Markdown of complex,
multi-column, and scanned documents.

Select it either way round:

- **per deployment** — `WISEAU_PDF_ENGINE=docling` makes it this Space's default;
- **per request** — `engine=docling` on a single conversion (ADR-025).

Whenever docling is selected, the fallback applies: if it is asleep, slow, or
down, the deterministic PyMuPDF/Mammoth parsers answer instead, so the service
degrades rather than fails. With no `WISEAU_DOCLING_BASE` set, docling is skipped
even when it is asked for.

The converter runs as its own service — see [`../docling/`](../docling) for the
image and deployment steps, [`../docs/tech-spec.md`](../docs/tech-spec.md) §11
for the selection/fallback rules, and ADR-013/014/018/027 for the reasoning.

## OCR (scanned & handwritten documents)

Image-only PDF pages and image uploads (`.png/.jpg/.tif/...`) are OCR'd. The
default engine is MuPDF's built-in **Tesseract** (installed in the Docker image;
nothing extra to `pip install`) — deterministic and strong on printed/scanned
text. For handwriting, enable the neural **EasyOCR** engine:

```bash
pip install -r requirements-ocr.txt
export WISEAU_OCR_ENGINE=easyocr
```

Tuning knobs (all optional): `WISEAU_OCR_MODE` (`auto`/`force`/`off`),
`WISEAU_OCR_DPI` (default `300`), `WISEAU_OCR_LANG` (default `eng`). See
[`../docs/tech-spec.md`](../docs/tech-spec.md) §10 and ADR-012 for the design.

## Run locally

Chromium and chromedriver must be on `PATH` (or set `CHROME_BIN` /
`CHROMEDRIVER_PATH`). For OCR of scanned/handwritten files, the `tesseract`
binary + language data must be installed (e.g. `apt install tesseract-ocr
tesseract-ocr-eng`); the Docker image includes them.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 7860
```

## Run with Docker

The image version-locks Chromium, Python, and system libraries.

```bash
docker build -t markdown-engine .
docker run -p 7860:7860 markdown-engine
```

## Deploying as a Hugging Face Space (#1)

1. Create a **Docker** Space (16 GB CPU tier) and push the contents of
   `backend/` to its repository root — a Space builds the `Dockerfile` it finds
   there, and the frontmatter at the top of this file is the Space card
   (`app_port: 7860` matches the `Dockerfile`, which already runs as UID 1000 as
   Spaces require).
2. Optionally set any of the variables in [Configuration](#configuration) in the
   Space's settings. None is required — the defaults are the deployed defaults —
   but out of the box a Space serves everything from the deterministic parsers.
3. To enable the high-fidelity half, deploy [`../docling/`](../docling) as Space
   #2 and set `WISEAU_DOCLING_BASE` / `WISEAU_DOCLING_API_KEY` /
   `WISEAU_DOCLING_TOKEN` here. Callers can then ask for it per request; add
   `WISEAU_PDF_ENGINE=docling` if you want it to be this Space's default. Steps and the live-verification order are in
   [`../docling/README.md`](../docling/README.md).
4. Confirm the Space is live: `GET /ping` returns the API version, and after a
   conversion `GET /metrics` shows it attributed to an engine. Then point the
   frontend at it — set the `MARKDOWN_API_BASE` repository variable (or edit
   `frontend/config.js`) and run the **Deploy frontend** workflow; see
   [`../frontend/README.md`](../frontend/README.md).

## Agent surfaces

- **MCP server** (`mcp_server.py`) exposes the engine as Model Context Protocol
  tools, over **stdio** (default — local clients like Claude Desktop/Code spawn
  it directly) or **`--transport streamable-http`** (serves the same tools over
  HTTP so any remote MCP client can register it as a connector; ADR-028). See
  [`../docs/mcp.md`](../docs/mcp.md).
- **Autonomous ingestion** (`monitor.py`) — a stdlib-only diff-checker that
  snapshots a URL's Markdown and reports changes on a schedule:
  ```bash
  python monitor.py --watch --interval 3600 https://example.com/article
  ```
  Both are thin HTTP clients over the routes above, so they inherit the same
  rate-limit and concurrency guards. See [`../docs/mcp.md`](../docs/mcp.md) §3.

## Observability

Logs are **JSON lines** by default (`WISEAU_LOG_FORMAT=text` for local work), one
object per record, with exactly one access line per request — uvicorn's own access
log is off so it does not duplicate it. Each line (and each response, as
`X-Request-ID`) carries a correlation id.

`GET /metrics` reports this process's counters: request and job timings, peak
concurrency and RSS (what to size `MAX_CONCURRENT_JOBS` against), and **engine
attribution** — which of `docling`/`pymupdf`/`mammoth`/`ocr`/`trafilatura`/
`markdownify` actually produced each conversion, plus docling's successes,
fallbacks by reason, and skips. That last part matters because the automatic
fallback makes a docling outage look like success:

```bash
curl -s localhost:7860/metrics | python -m json.tool
# engines: {"docling": 0, "pymupdf": 38, "ocr": 3}  <- docling has been down all week
```

Aggregates only (no URLs, filenames, or content — the endpoint is public), and
they reset with the process. See [`../docs/tech-spec.md`](../docs/tech-spec.md)
§12 and ADR-019.

## Configuration

| Variable              | Default   | Purpose                                        |
| --------------------- | --------- | ---------------------------------------------- |
| `PORT`                | `7860`    | Listen port.                                   |
| `MAX_CONCURRENT_JOBS` | `4`       | Global concurrency ceiling for heavy jobs.     |
| `MAX_UPLOAD_BYTES`    | `26214400`| Upload size limit (25 MB).                     |
| `CHROME_BIN`          | —         | Path to the Chromium binary.                   |
| `CHROMEDRIVER_PATH`   | —         | Path to chromedriver.                          |
| `WISEAU_PDF_ENGINE`   | `pymupdf` | **Default** engine: `pymupdf` (the fast local parser) or `docling`. A request's `engine` overrides it. |
| `WISEAU_DOCLING_BASE` | —         | docling-serve base URL. Unset ⇒ docling skipped even when selected. |
| `WISEAU_DOCLING_TOKEN`| —         | Sent as `Authorization: Bearer` (private-Space gateway). |
| `WISEAU_DOCLING_API_KEY` | —      | Sent as `X-Api-Key` (docling-serve's `DOCLING_SERVE_API_KEY`). |
| `WISEAU_DOCLING_TIMEOUT` | `120`  | Seconds to wait on docling before falling back. |
| `WISEAU_DOCLING_PATH` | `/v1/convert/file` | docling-serve convert endpoint path. |
| `WISEAU_ALLOW_PRIVATE_URLS` | unset | Allow `/convert/url` to fetch non-public addresses (ADR-021). |
| `WISEAU_LOG_FORMAT`   | `json`    | `json` (one object per line) or `text` (human-readable). |
| `WISEAU_LOG_LEVEL`    | `INFO`    | Root log level.                                |

Per-IP rate limits (`60/min`, `1000/day` default; `20/min` on convert routes)
are configured in `main.py`. The defaults reach undecorated routes only because
`SlowAPIMiddleware` is installed — remove it and they bind nothing while the
convert routes keep working, so the loss is silent. `/ping` is exempt.
