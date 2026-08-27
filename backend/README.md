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
| POST   | `/convert/url`  | `{ "url": "...", "engine": "auto", "split_chapters": false }` → Markdown JSON. |
| POST   | `/convert/file` | multipart `file` (PDF/DOCX/image) + optional `engine`, `split_chapters` → Markdown JSON.|
| POST   | `/convert/batch`| multipart `files` repeated per document + optional `engine` → one result each. |
| POST   | `/keywords`     | `{ "markdown": "...", "methods": [...], "top_k": 20, "prepend_table": false }` → ranked keywords. |
| GET    | `/metrics`      | Per-process operational counters (see below).    |

`/convert/url` refuses a URL that resolves to a loopback/private/link-local
address with a **400** — the endpoint is public and the fetch happens inside the
container, so an unguarded renderer is an SSRF primitive. Set
`WISEAU_ALLOW_PRIVATE_URLS=1` on a self-hosted deployment that converts its own
intranet. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §13 and ADR-021.

All three convert endpoints take an optional **`engine`**: `pymupdf` (fast,
deterministic — what `WISEAU_PDF_ENGINE` defaults to), `docling` (highest
fidelity, far slower on free CPU), or `auto` (the parameter's default — use this
deployment's `WISEAU_PDF_ENGINE`). An unknown value is a **400**. Requesting
`docling` does not disable the automatic fallback, and `GET /ping` lists both the
names this build accepts and the deployment's `default_engine`. See ADR-025 and
ADR-027.

The two single-document endpoints also take an optional **`split_chapters`**
(default `false`). With it set,
the response carries the document *also* split into chapters — each with a title,
its Markdown, and a numbered filename — so a long PDF can be saved one file per
chapter:

```bash
curl -X POST localhost:7860/convert/file -F 'file=@book.pdf' -F 'split_chapters=true'
# -> {..., "chapter_detection": "toc",
#     "chapters": [{"title": "Chapter 1: The Arrival", "level": 2,
#                   "filename": "01-chapter-1-the-arrival.md", ...}, ...]}
```

Chapters are found from the document's own contents page where it has one,
otherwise from its heading structure, otherwise from plain-text "Chapter N"
lines; `chapter_detection` says which. A document with no chapter structure is
returned whole (`"chapter_detection": "none"`). The chapters partition the
document — concatenating them reproduces it. Left off, the response is exactly
what it has always been. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §15
and ADR-030.

`/convert/batch` converts **several documents in one request** — the `files` part
repeated once per document — so a folder is one call rather than one per file
against a `20/minute` limit:

```bash
curl -X POST localhost:7860/convert/batch \
     -F 'files=@annual-report.pdf' -F 'files=@minutes.docx'
# -> {"count": 2, "succeeded": 2, "failed": 0,
#     "results": [{"status": "ok", "source": "annual-report.pdf",
#                  "filename": "annual-report.md", "markdown": "# ...", ...}, ...]}
```

Results come back **in the order they were sent**, each with the filename to save
it as (made unique within the batch). A document that cannot be converted is an
entry with `"status": "error"` and **no filename** — one bad file never costs the
others, and "write every result that has a filename" is a complete save loop. A
batch is N conversions for one rate-limit token, so it has its own bounds:
`5/minute`, `MAX_BATCH_FILES` documents, `MAX_BATCH_BYTES` in total, and one job
slot per document rather than one for the whole run. `split_chapters` is **not**
available here — bulk conversion and chapter splitting are mutually exclusive for
now, and asking for both is a 400. See
[`../docs/tech-spec.md`](../docs/tech-spec.md) §16 and ADR-031.

`/keywords` ranks what an already-converted document is **about**. It takes the
Markdown itself rather than a URL or an upload, because keyword extraction is
something a reader asks for *after* seeing the conversion — a flag on the convert
routes would mean re-converting to change your mind about the methods:

```bash
curl -X POST localhost:7860/keywords \
     -H 'Content-Type: application/json' \
     -d '{"markdown":"# Coastal Inundation ...","methods":["all"],"top_k":10}'
```

Several methods rank the terms independently and the **rankings** are fused (their
scores are on incomparable scales), so every keyword reports `agreement` — how
many methods found it — plus the rank and native score each one gave it.
`frequency` is built in and always available; `yake` ships in `requirements.txt`;
`spacy` and `keybert` are opt-in via `requirements-keywords.txt` and are reported
in `methods_skipped` when they are not installed, rather than failing the request.
`prepend_table: true` also returns the document with the keywords as a Markdown
table at the top. See [`../docs/tech-spec.md`](../docs/tech-spec.md) §17 and
ADR-032.

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

## Deploying

Any host that can build this `Dockerfile` and give it a public HTTPS URL works —
the container binds `$PORT`, so Cloud Run (8080), Render (10000) and Hugging Face
Spaces (7860) all need no change. **[`../docs/hosting.md`](../docs/hosting.md) is
the click-by-click guide**, including cost guards and how to connect an LLM to
the resulting `/mcp` URL. The Space-specific steps follow.

### As a Hugging Face Space (#1)

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
4. Confirm the Space is live: `GET /ping` returns the API version and the
   `mcp_endpoint` path, and after a conversion `GET /metrics` shows it attributed
   to an engine. Then point the
   frontend at it — set the `MARKDOWN_API_BASE` repository variable (or edit
   `frontend/config.js`) and run the **Deploy frontend** workflow; see
   [`../frontend/README.md`](../frontend/README.md).

## Agent surfaces

- **MCP endpoint** — this app serves the Model Context Protocol tools itself at
  `POST /mcp` (ADR-029), so a deployed backend's URL *is* a connector URL:
  `https://<your deployment>/mcp`, pasteable into claude.ai, Claude Code,
  ChatGPT, or any agent framework that takes a remote MCP server. `GET /ping`
  reports the path as `mcp_endpoint` (`WISEAU_MCP_PATH` can move it;
  `WISEAU_MCP_MOUNT=0` turns it off). Deploying one: [`../docs/hosting.md`](../docs/hosting.md).
- **MCP server as a process** (`mcp_server.py`) — the same tools over **stdio**
  (local clients like Claude Desktop/Code spawn it directly) or
  **`--transport streamable-http`** on a port of its own (ADR-028). Use it for a
  local agent; the hosted endpoint above is the deployment path. See
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
# engines:  {"docling": 0, "pymupdf": 38, "ocr": 3}   <- docling has been down all week
# chapters: {"requested": 12, "split": 11, "sections": 74,
#            "by_method": {"toc": 9, "headings": 2, "none": 1}}
# batches:  {"requested": 4, "files": 37, "failed": 1, "largest": 20}
# keywords: {"requested": 9, "keywords": 178, "empty": 1,
#            "by_method": {"frequency": 9, "yake": 9},   <- the extras are installed
#            "skipped": {"keybert": 2}}                     but nobody asks for them
```

`batches` is there because a batch's real cost is invisible in a request count —
thirty documents and one document are both a single `POST /convert/batch` — so
`files` is the number that sizes `MAX_BATCH_FILES`, and a climbing `failed` means
callers are sending something the engine cannot read.

Aggregates only (no URLs, filenames, or content — the endpoint is public), and
they reset with the process. See [`../docs/tech-spec.md`](../docs/tech-spec.md)
§12 and ADR-019.

## Configuration

| Variable              | Default   | Purpose                                        |
| --------------------- | --------- | ---------------------------------------------- |
| `PORT`                | `7860`    | Listen port. Cloud Run/Render inject their own; the image binds it. |
| `WISEAU_MCP_MOUNT`    | `1`       | Serve the MCP endpoint from this app; `0` disables it. |
| `WISEAU_MCP_PATH`     | `/mcp`    | Path of that endpoint — set an unguessable one to keep the connector URL secret. |
| `WISEAU_MCP_ALLOWED_HOSTS` | —    | Hostnames the MCP endpoint accepts; unset/`*` disables the check (what a public deployment needs). |
| `MAX_CONCURRENT_JOBS` | `4`       | Global concurrency ceiling for heavy jobs.     |
| `MAX_UPLOAD_BYTES`    | `26214400`| Upload size limit (25 MB), per document.       |
| `MAX_BATCH_FILES`     | `20`      | Most documents one `/convert/batch` request may carry (ADR-031). |
| `MAX_BATCH_BYTES`     | `52428800`| Total upload bytes one batch may carry (50 MB). |
| `CHROME_BIN`          | —         | Path to the Chromium binary.                   |
| `CHROMEDRIVER_PATH`   | —         | Path to chromedriver.                          |
| `WISEAU_PDF_ENGINE`   | `pymupdf` | **Default** engine: `pymupdf` (the fast local parser) or `docling`. A request's `engine` overrides it. |
| `WISEAU_DOCLING_BASE` | —         | docling-serve base URL. Unset ⇒ docling skipped even when selected. |
| `WISEAU_DOCLING_TOKEN`| —         | Sent as `Authorization: Bearer` (private-Space gateway). |
| `WISEAU_DOCLING_API_KEY` | —      | Sent as `X-Api-Key` (docling-serve's `DOCLING_SERVE_API_KEY`). |
| `WISEAU_DOCLING_TIMEOUT` | `120`  | Seconds to wait on docling before falling back. |
| `WISEAU_DOCLING_PATH` | `/v1/convert/file` | docling-serve convert endpoint path. |
| `MAX_KEYWORD_CHARS`   | `2000000` | Longest document `/keywords` accepts (ADR-032); past it, a 413. |
| `WISEAU_KEYWORD_METHODS` | unset  | Default keyword methods: a comma list, `auto` (`frequency,yake`), or `all`. Set `all` only after installing `requirements-keywords.txt`. |
| `WISEAU_KEYWORD_LANG` | `en`      | Language the language-aware keyword methods assume. |
| `WISEAU_KEYWORD_MAX_CHARS` | `400000` | How much of a document any keyword method reads; beyond it the answer says it was truncated. |
| `WISEAU_SPACY_MODEL`  | `en_core_web_sm` | spaCy pipeline for the `spacy` method. |
| `WISEAU_KEYBERT_MODEL`| `all-MiniLM-L6-v2` | Sentence-transformer for the `keybert` method. |
| `WISEAU_ALLOW_PRIVATE_URLS` | unset | Allow `/convert/url` to fetch non-public addresses (ADR-021). |
| `WISEAU_LOG_FORMAT`   | `json`    | `json` (one object per line) or `text` (human-readable). |
| `WISEAU_LOG_LEVEL`    | `INFO`    | Root log level.                                |

Per-IP rate limits (`60/min`, `1000/day` default; `20/min` on convert routes)
are configured in `main.py`. The defaults reach undecorated routes only because
`SlowAPIMiddleware` is installed — remove it and they bind nothing while the
convert routes keep working, so the loss is silent. `/ping` is exempt.
