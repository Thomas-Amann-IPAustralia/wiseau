# Wiseau

**Oh hi Mark(down)** — the Universal Markdown Ingestion Engine.

Wiseau is a self-hosted service that turns **web pages, PDFs, Word documents and
images** into clean, structured Markdown. One contract serves two kinds of
consumer: people, through a static web UI, and LLMs/agents, through an HTTP API,
an OpenAPI schema and a hosted [MCP](https://modelcontextprotocol.io) endpoint.

[Live web UI](https://thomas-amann-ipaustralia.github.io/wiseau/) ·
[Interactive API docs](#api) (`/docs` on any deployment) ·
[Hosting guide](docs/hosting.md) · [Agent integration](docs/mcp.md)

## What it does

- **Web pages** — rendered in headless Chromium (Selenium-stealth) and extracted
  with Trafilatura, so JavaScript-heavy sites work. A URL that points at a PDF is
  downloaded and handled as a PDF.
- **PDFs** — text-layer PDFs go through PyMuPDF; scanned and image-only pages are
  OCR'd with Tesseract. Optional passes recover tables (Table Transformer),
  headings and paragraph breaks (PP-DocLayoutV3), and document structure:
  heading levels, running heads and page furniture, paragraph continuation,
  captions, footnotes and tables that continue across pages.
- **Word documents** — DOCX through Mammoth.
- **Images** — PNG, JPG, TIFF and similar are OCR'd.
- **Chapters** — optionally split a long document into one numbered `.md` file per
  chapter, found from its contents page, headings, or "Chapter N" lines. It
  reports which signal fired and returns the document whole if the result looks
  implausible.
- **Bulk conversion** — send a whole folder in one request and get one Markdown
  file back for each document, or one ZIP from the UI.
- **Keywords** — rank what an already-converted document is about, by several
  methods fused by rank.
- **Figures** — `POST /convert/bundle` returns a ZIP of the Markdown, a JSON
  description of the document, and the figure and table images.

## Design values

1. **Faithful first.** The aim is the most accurate Markdown of the source. Text
   is taken as the engine read it, and structure is inferred separately, so a
   structure pass can never alter a word.
2. **Fast by default.** The engine that runs when nobody chose one is the fast
   local parser. Slower, heavier options are a deliberate choice.
3. **Resilient.** If docling is selected and is slow, asleep or down, the local
   parsers answer instead.
4. **Decoupled.** The frontend, backend and converter services talk only over HTTP.
5. **Memory-aware.** Headless browsing and ML inference are heavy, so work runs
   behind a concurrency ceiling and per-IP rate limits.
6. **Agent-native.** The API is a first-class integration surface.
7. **Open but protected.** The API is public, guarded by fair-use limits rather
   than origin locks.

The default paths (the normalizer, PyMuPDF/Mammoth, Trafilatura, the Fast and
Accurate OCR paths) are deterministic. The opt-in docling path is not, and is not
meant to be.

## Engines

Pick one per request with `engine`, or per deployment with `WISEAU_PDF_ENGINE`.

| `engine` | UI label | What it is |
| -------- | -------- | ---------- |
| `auto` | Auto | Whatever this deployment is configured to use. |
| `fast` | Fast | Text-layer pages, DOCX and URLs are handled exactly as in Accurate; scans are read by Tesseract alone. Several times quicker on scans, but no heading levels and tables come out as rows of text. |
| `pymupdf` | Accurate | The default. PyMuPDF/Mammoth, plus Tesseract with the table and layout passes on scanned pages. |
| `docling` | docling (experimental) | An ML converter running as a separate service. Slow on free CPU, with automatic fallback. On the project's benchmarks it did not beat Accurate on structure. |

Digital documents come out the same from Fast and Accurate. The measurements
behind the choices are in [`docs/benchmarks/`](docs/benchmarks/).

## Repository layout

```
wiseau/
├── backend/     FastAPI service: routes, rate limits, the MCP endpoint, parsers/
├── frontend/    Static UI (plain HTML, CSS and JS; no build step) for GitHub Pages
├── docling/     docling-serve image for the optional converter service
├── glm-ocr/     Optional experimental OCR service
├── benchmarks/  The bake-off harness and reference sets
├── pdf-test/    Test PDFs used by the benchmarks
└── docs/        Specification, decisions, roadmap, benchmark write-ups
```

## Quick start

**Backend** — needs Python 3.11, plus Chromium and chromedriver on `PATH` (or
`CHROME_BIN` / `CHROMEDRIVER_PATH`), and the `tesseract` binary for OCR:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 7860
```

**Backend with Docker** (matches production; pins Chromium and Python and includes
Tesseract and the table and layout models):

```bash
cd backend
docker build -t markdown-engine .
docker run -p 7860:7860 markdown-engine
# lean image without PyTorch: docker build --build-arg TABLES=0 -t markdown-engine .
```

**Frontend:**

```bash
cd frontend
python -m http.server 8000      # then open http://localhost:8000
```

`frontend/config.js` holds `MARKDOWN_API_BASE`, the backend the UI talks to.

**Try it:**

```bash
curl localhost:7860/ping

curl -X POST localhost:7860/convert/url \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com"}'

curl -X POST localhost:7860/convert/file -F 'file=@report.pdf'
```

## API

Interactive docs are at `/docs` and the schema at `/openapi.json`.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/ping` | Liveness, API version, available engines, MCP path. |
| POST | `/convert/url` | `{"url": "...", "engine": "auto", "split_chapters": false}` → Markdown JSON. |
| POST | `/convert/file` | Multipart `file` (PDF, DOCX or image) → Markdown JSON. |
| POST | `/convert/batch` | Multipart `files`, one per document → one result each, in send order. |
| POST | `/convert/bundle` | One document → ZIP of Markdown, JSON and figure images. |
| POST | `/convert/{url,file,batch}/stream` | The same conversions as NDJSON, reporting each stage and page as it finishes. |
| POST | `/keywords` | `{"markdown": "..."}` → ranked keywords. |
| POST | `/keywords/batch` | Several documents' keywords in one request. |
| GET | `/usage` | CPU time this month against the deployment's free allowance. |
| GET | `/metrics` | Per-process counters, including which engine produced each result. |

`/convert/url` refuses addresses that resolve to private or loopback networks,
since the fetch happens inside the container. Set `WISEAU_ALLOW_PRIVATE_URLS=1`
on a self-hosted deployment that needs to convert its own intranet.

Full contract: [`docs/tech-spec.md`](docs/tech-spec.md). Backend details and the
complete list of configuration variables: [`backend/README.md`](backend/README.md).

## Using it from an LLM

A deployed backend serves MCP itself at `https://<your deployment>/mcp`. Paste that
URL into Claude, ChatGPT or any client that takes a remote MCP server. The tools
are `convert_url`, `convert_file`, `convert_batch`, `extract_keywords`,
`extract_keywords_batch` and `ping`. The same tools also run as a local stdio
process (`backend/mcp_server.py`) for Claude Desktop or Claude Code, and
`/openapi.json` doubles as a function-calling schema.

`backend/monitor.py` is a stdlib-only watcher that snapshots a URL's Markdown on a
schedule and reports changes.

See [`docs/mcp.md`](docs/mcp.md).

## Deploying

The backend is one container. It binds `$PORT`, so Google Cloud Run, Render and
Hugging Face Spaces all work unchanged. The frontend deploys to GitHub Pages
through `.github/workflows/deploy-frontend.yml`, with the backend URL taken from
the `MARKDOWN_API_BASE` repository variable.

The click-by-click guide, including cost guards and connecting an LLM, is
[`docs/hosting.md`](docs/hosting.md). docling, when wanted, is deployed as its own
service ([`docling/`](docling/)) and the backend is pointed at it with
`WISEAU_DOCLING_BASE`.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest                          # browserless suite; the URL worker is mocked
WISEAU_LIVE_BROWSER=1 pytest    # also runs the live-Chromium tests
```

The suite needs the `tesseract` binary for its OCR tests. docling is tested
against a mocked transport, so no live docling service is needed. The optional
keyword methods (spaCy, KeyBERT) are installed from `requirements-keywords.txt`;
their tests skip themselves without them. CI runs the suite on every backend
change.

## Project documentation

This project is built by a series of Claude Code sessions handing work to one
another, so the documents are kept current.

| Document | Purpose |
| -------- | ------- |
| [`CLAUDE.md`](CLAUDE.md) | Working rules for contributors, human or agent. Read first. |
| [`STATUS.md`](STATUS.md) | What is done, what was verified, what is next. |
| [`docs/project-brief.md`](docs/project-brief.md) | The vision and design principles. |
| [`docs/tech-spec.md`](docs/tech-spec.md) | API contracts, modules, configuration, errors. |
| [`docs/decisions.md`](docs/decisions.md) | The decision log (ADRs), with the reasoning. |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased task list. |
| [`docs/mcp.md`](docs/mcp.md) | MCP tools and function calling. |
| [`docs/hosting.md`](docs/hosting.md) | Deployment and connecting an LLM. |
| [`docs/benchmarks/`](docs/benchmarks/) | Measured comparisons of engines, OCR and structure passes. |

## License

[MIT](LICENSE)
