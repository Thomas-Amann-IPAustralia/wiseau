# Backend — Markdown Ingestion Engine

FastAPI microservice that converts URLs, PDFs, DOCX documents, and images into
clean Markdown. Scanned and handwritten PDFs (and image uploads) are OCR'd
automatically. See [`../docs/project-brief.md`](../docs/project-brief.md) for the
full design.

## Endpoints

| Method | Path            | Purpose                                          |
| ------ | --------------- | ------------------------------------------------ |
| GET    | `/ping`         | Liveness/readiness check (rate-limit exempt).    |
| POST   | `/convert/url`  | `{ "url": "..." }` → Markdown JSON.              |
| POST   | `/convert/file` | multipart `file` (PDF/DOCX/image) → Markdown JSON.|
| GET    | `/metrics`      | Per-process operational counters (see below).    |

Interactive docs and the machine-readable schema for LLM/MCP integration are
served at `/docs` and `/openapi.json`.

`/convert/url` also handles **direct-PDF links** (a `.pdf` URL, or any URL that
serves a PDF): the bytes are downloaded through the browser's own session and run
through the document pipeline below, rather than extracting Chrome's empty PDF
viewer. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §4 and ADR-017.

## High-fidelity conversion (docling)

Document conversion is **docling-first with automatic fallback**: with a
docling-serve service configured, PDFs/DOCX/images are converted there for much
more faithful Markdown; if it is asleep, slow, or down, the deterministic
PyMuPDF/Mammoth parsers answer instead, so the service degrades rather than
fails. With no `WISEAU_DOCLING_BASE` set, docling is simply skipped.

The converter runs as its own service — see [`../docling/`](../docling) for the
image and deployment steps, [`../docs/tech-spec.md`](../docs/tech-spec.md) §11
for the selection/fallback rules, and ADR-013/014/018 for the reasoning.

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

## Agent surfaces

- **MCP server** (`mcp_server.py`) exposes the engine as Model Context Protocol
  tools. See [`../docs/mcp.md`](../docs/mcp.md).
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
# engines: {"docling": 0, "pymupdf": 41}  <- docling has been down all week
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
| `WISEAU_PDF_ENGINE`   | `docling` | `docling` (default) or `pymupdf` to force the local parser. |
| `WISEAU_DOCLING_BASE` | —         | docling-serve base URL. Unset ⇒ docling skipped. |
| `WISEAU_DOCLING_TOKEN`| —         | Sent as `Authorization: Bearer` (private-Space gateway). |
| `WISEAU_DOCLING_API_KEY` | —      | Sent as `X-Api-Key` (docling-serve's `DOCLING_SERVE_API_KEY`). |
| `WISEAU_DOCLING_TIMEOUT` | `120`  | Seconds to wait on docling before falling back. |
| `WISEAU_LOG_FORMAT`   | `json`    | `json` (one object per line) or `text` (human-readable). |
| `WISEAU_LOG_LEVEL`    | `INFO`    | Root log level.                                |

Per-IP rate limits (`60/min`, `1000/day` default; `20/min` on convert routes)
are configured in `main.py`.
