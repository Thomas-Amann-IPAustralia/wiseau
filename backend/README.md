# Backend — Markdown Ingestion Engine

FastAPI microservice that converts URLs, PDFs, and DOCX documents into clean
Markdown. See [`../docs/project-brief.md`](../docs/project-brief.md) for the
full design.

## Endpoints

| Method | Path            | Purpose                                          |
| ------ | --------------- | ------------------------------------------------ |
| GET    | `/ping`         | Liveness/readiness check (rate-limit exempt).    |
| POST   | `/convert/url`  | `{ "url": "..." }` → Markdown JSON.              |
| POST   | `/convert/file` | multipart `file` (PDF/DOCX) → Markdown JSON.     |

Interactive docs and the machine-readable schema for LLM/MCP integration are
served at `/docs` and `/openapi.json`.

## Run locally

Chromium and chromedriver must be on `PATH` (or set `CHROME_BIN` /
`CHROMEDRIVER_PATH`).

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

## Configuration

| Variable              | Default   | Purpose                                        |
| --------------------- | --------- | ---------------------------------------------- |
| `PORT`                | `7860`    | Listen port.                                   |
| `MAX_CONCURRENT_JOBS` | `4`       | Global concurrency ceiling for heavy jobs.     |
| `MAX_UPLOAD_BYTES`    | `26214400`| Upload size limit (25 MB).                     |
| `CHROME_BIN`          | —         | Path to the Chromium binary.                   |
| `CHROMEDRIVER_PATH`   | —         | Path to chromedriver.                          |

Per-IP rate limits (`60/min`, `1000/day` default; `20/min` on convert routes)
are configured in `main.py`.
