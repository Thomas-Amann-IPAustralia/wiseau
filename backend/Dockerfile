# Version-locked image for the Markdown ingestion engine.
# Chromium + chromedriver are installed at the system level so Selenium renders
# JS-heavy pages reliably inside the Hugging Face Spaces container.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# System dependencies: Chromium + driver + fonts for rendering, and Tesseract
# (with English language data) for OCR of scanned / handwritten PDFs and images.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        libnss3 \
        libxss1 \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The MCP dependencies are installed alongside the base ones because the
# deployed backend serves the MCP endpoint itself (ADR-029) — one container for
# both the REST API and the agent-facing connector.
COPY requirements.txt requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-mcp.txt

COPY . .

# Hugging Face Spaces runs containers as UID 1000 (non-root).
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860
# Shell form so `$PORT` is expanded at start-up: Hugging Face Spaces wants 7860,
# while Cloud Run and Render inject a port of their own choosing and expect the
# container to listen on it (ADR-029). `mcp_server` reads the same variable, so
# its loopback calls follow automatically.
# `--no-access-log`: the app's own middleware already emits one structured
# (JSON) line per request, so uvicorn's plain-text access log is a duplicate.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860} --no-access-log
