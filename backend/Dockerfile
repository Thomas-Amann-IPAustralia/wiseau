# Version-locked image for the Markdown ingestion engine.
# Chromium + chromedriver are installed at the system level so Selenium renders
# JS-heavy pages reliably inside the Hugging Face Spaces container.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# System dependencies:
#  - Chromium + driver + fonts: JS-aware page rendering.
#  - Tesseract + English data: the zero-dependency OCR fallback engine.
#  - libgl1 + libglib2.0-0: shared libraries OpenCV needs, pulled in by the
#    default RapidOCR engine (opencv-python), absent from the slim base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        libnss3 \
        libxss1 \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces runs containers as UID 1000 (non-root).
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
