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

# The model passes on scanned pages, on by default in this image: tables read
# cell by cell by Table Transformer (ADR-046/047) and layout regions from
# PP-DocLayoutV3 (ADR-048: headings, block boundaries, captions, page furniture).
# They add CPU PyTorch and three pinned models (~1.3 GB of image, ~1.5 GB more
# memory, about +5-8 s per scanned page on one thread), which suits the 16 GB Space.
# For a host with 2 GiB or less (Cloud Run, Render; docs/hosting.md) build with
#   docker build --build-arg TABLES=0 .
# or set WISEAU_TABLES / WISEAU_LAYOUT to empty at run time. Without the packages a
# pass is skipped, never an error. The models are fetched here, at build time, and
# the container runs offline from them: no conversion depends on Hugging Face.
ARG TABLES=1
ENV HF_HOME=/opt/hf \
    WISEAU_TABLES=tatr \
    WISEAU_TABLE_THREADS=1 \
    WISEAU_LAYOUT=ppdl \
    WISEAU_LAYOUT_THREADS=1
COPY requirements-tables.txt fetch_table_models.py ./
COPY parsers/tables.py ./pinned_tables.py
COPY parsers/layout.py ./pinned_layout.py
RUN if [ "$TABLES" = "1" ]; then \
        pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
            torch==2.15.0 torchvision==0.29.2 \
        && pip install --no-cache-dir -r requirements-tables.txt \
        && python fetch_table_models.py pinned_tables.py:2 pinned_layout.py:1 \
        && chmod -R a+rX /opt/hf; \
    fi \
    && rm pinned_tables.py pinned_layout.py
ENV HF_HUB_OFFLINE=1
# Structure recovery over the IR (ADR-049..055): heading levels, page furniture,
# paragraph continuation, caption links, footnotes, continued tables. No model
# and no dependency, so it is on in the lean image too; ~60 ms a document.
# Faithful by default (every word kept); reader mode is per request (`reader`).
ENV WISEAU_STRUCTURE=all

COPY . .

# Hugging Face Spaces runs containers as UID 1000 (non-root).
# /opt/hf (the models) is left root-owned and world-readable: chown-ing it
# would copy 230 MB of weights into another layer, and it is only read.
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
