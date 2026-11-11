"""GLM-OCR converter service — the HTTP face wiseau's backend talks to (ADR-034).

One endpoint that matters, shaped like docling-serve's so the backend's client is
a near-copy of ``docling_client``:

``POST /v1/convert/file`` (multipart, one file under ``files``) →
``{"markdown": "...", "pages": N, "duration_ms": ...}``

Inside, it runs the official GLM-OCR SDK pipeline *in process* — rasterize the
PDF, PP-DocLayoutV3 layout detection, region-by-region recognition, merge into
Markdown — with recognition delegated to ``model_server.py`` on localhost (the
SDK only speaks the OpenAI chat protocol to its model). Running the SDK's layout
stage is not optional polish: the GLM-OCR report itself says the small model is
"highly susceptible to hallucinations and repetitive generation" on complex
layouts without it.

Guards, because this box has exactly one client and CPU inference costs minutes
per page:

* ``GLM_OCR_API_KEY`` — when set, requests must carry it as ``X-Api-Key``.
* ``GLM_OCR_MAX_PAGES`` (default 20) — longer PDFs are refused with **413**, which
  the backend classifies as a bad document and answers with its fast parser.
* ``GLM_OCR_MAX_FILE_MB`` (default 25).
* One document at a time (a lock): the model server serializes generation
  anyway, and two documents interleaving would only make both late.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time

import pypdfium2
from flask import Flask, jsonify, request
from glmocr import GlmOcr

logger = logging.getLogger("glm_ocr_service")

PORT = int(os.environ.get("PORT", "7860"))
MODEL_PORT = int(os.environ.get("GLM_OCR_PORT", "8080"))
API_KEY = os.environ.get("GLM_OCR_API_KEY", "").strip()
MAX_PAGES = int(os.environ.get("GLM_OCR_MAX_PAGES", "20"))
MAX_BYTES = int(float(os.environ.get("GLM_OCR_MAX_FILE_MB", "25")) * 1024 * 1024)
LAYOUT_MODEL = os.environ.get("GLM_OCR_LAYOUT_MODEL", "PaddlePaddle/PP-DocLayoutV3_safetensors")
PDF_DPI = int(os.environ.get("GLM_OCR_PDF_DPI", "200"))
# Where the SDK runs its PP-DocLayoutV3 stage: cpu (the Space), or a CUDA device
# for the GPU benchmark (benchmarks/bakeoff/kaggle/e2e_kernel.py).
LAYOUT_DEVICE = os.environ.get("GLM_OCR_LAYOUT_DEVICE", "cpu").strip() or "cpu"
# Seconds the SDK waits on a single region. A dense table region can run to
# thousands of tokens, which on CPU is many minutes.
REGION_TIMEOUT = int(os.environ.get("GLM_OCR_REGION_TIMEOUT", "1800"))
# The SDK asks every region for repetition_penalty 1.1. On this model that is the
# cause of the runaways: on V16 p11 a dotted-leader index looped on ". . ." to the
# token cap (5 min, half the page lost) at 1.1 — in bf16 *and* float32 — and
# finished cleanly in 50 s at 1.0. Over the 166 regions of the 2026-11-01 sample,
# 1.0 never looped and was more faithful on 32 of them (kept periods, spaces and
# opening quotes; transcribed 61 vs 36 rows of the V16 p90 table). Values above 1
# start misspelling repeated letters ("Zarina" for Zanrina at 1.3). See
# docs/benchmarks/2026-11-02-glm-ocr-diagnosis.md.
REPETITION_PENALTY = float(os.environ.get("GLM_OCR_REPETITION_PENALTY", "1.0"))

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}

parser = GlmOcr(
    mode="selfhosted",
    ocr_api_host="127.0.0.1",
    ocr_api_port=MODEL_PORT,
    layout_device=LAYOUT_DEVICE,
    _dotted={
        "pipeline.layout.model_dir": LAYOUT_MODEL,
        "pipeline.page_loader.pdf_dpi": PDF_DPI,
        "pipeline.ocr_api.request_timeout": REGION_TIMEOUT,
        "pipeline.page_loader.repetition_penalty": REPETITION_PENALTY,
        # The model server runs one generation at a time; a deep worker pool
        # would only queue requests behind it and hold their images in memory.
        "pipeline.max_workers": 2,
    },
)
_lock = threading.Lock()
app = Flask(__name__)


def _error(status: int, detail: str):
    return jsonify({"detail": detail}), status


def _page_count(path: str, ext: str) -> int:
    if ext != ".pdf":
        return 1
    pdf = pypdfium2.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


@app.post("/v1/convert/file")
def convert_file():
    if API_KEY and request.headers.get("X-Api-Key", "") != API_KEY:
        return _error(401, "missing or wrong X-Api-Key")
    upload = request.files.get("files")
    if upload is None:
        return _error(400, "send the document as multipart field 'files'")
    filename = upload.filename or "document"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED:
        return _error(415, f"GLM-OCR reads PDFs and images, not '{ext or 'unknown'}'")
    data = upload.read()
    if len(data) > MAX_BYTES:
        return _error(413, f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")

    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "input" + ext)
        with open(path, "wb") as handle:
            handle.write(data)
        try:
            pages = _page_count(path, ext)
        except Exception as exc:  # noqa: BLE001 - a corrupt PDF is the caller's problem
            return _error(422, f"could not read the document: {exc}")
        if pages > MAX_PAGES:
            return _error(413, f"document has {pages} pages; this service accepts at most {MAX_PAGES}")

        with _lock:
            started = time.perf_counter()
            try:
                result = parser.parse(path)
            except Exception as exc:  # noqa: BLE001 - surface as a 5xx: the backend falls back
                logger.exception("GLM-OCR pipeline failed")
                return _error(500, f"GLM-OCR pipeline failed: {exc}")
            elapsed_ms = (time.perf_counter() - started) * 1000

    results = result if isinstance(result, list) else [result]
    markdown = "\n\n".join((r.markdown_result or "").strip() for r in results).strip()
    logger.info("converted %s: %d page(s), %d chars in %.0f ms", filename, pages, len(markdown), elapsed_ms)
    return jsonify({"markdown": markdown, "pages": pages, "duration_ms": round(elapsed_ms, 1)})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "max_pages": MAX_PAGES})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from waitress import serve

    serve(app, host="0.0.0.0", port=PORT, threads=4, channel_timeout=3600)
