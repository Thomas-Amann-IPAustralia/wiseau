# GLM-OCR converter — an optional third Space, the opt-in `glm-ocr` engine (ADR-034).
#
# Same role and shape as docling/ (ADR-015/018): a separate box the backend calls
# over HTTP at WISEAU_GLM_OCR_BASE, never reached by the public. It runs two
# processes (start.sh):
#
#   model_server.py  zai-org/GLM-OCR via transformers, OpenAI-compatible, :8080
#   service.py       the GLM-OCR SDK pipeline (PP-DocLayoutV3 layout + region
#                    recognition) behind POST /v1/convert/file, :7860
#
# CPU-only on purpose: it is what the free tier offers. Measured cost on a
# 4-vCPU box is *minutes per page* (ADR-034), so the service caps documents at
# GLM_OCR_MAX_PAGES and the backend falls back to its fast parser past that.
# On GPU hardware, replace model_server.py with vLLM/SGLang serving the same
# model on :8080 — the SDK and service.py are unchanged.
#
# Weights are baked in at pinned revisions so a cold start never downloads
# ~2.7 GB inside the first request (the reason ADR-018 chose a weights-baked
# docling image).
#
#   docker build -t wiseau-glm-ocr glm-ocr/
#   docker run -p 7860:7860 -e GLM_OCR_API_KEY=dev-key wiseau-glm-ocr
#   curl localhost:7860/health
FROM python:3.12-slim

# pypdfium2/opencv wheels are self-contained; libgomp is torch's OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.15.0 torchvision==0.29.2 \
    && pip install --no-cache-dir -r requirements.txt

# Model weights, revision-pinned (the commits the 2026-11-01 bake-off used).
ENV HF_HOME=/models
RUN python -c "from huggingface_hub import snapshot_download as d; \
d('zai-org/GLM-OCR', revision='2e85a62840ccac27daa451df36c736c4636b8628'); \
d('PaddlePaddle/PP-DocLayoutV3_safetensors', revision='e4f489dc536556fc1be02b973ba06756e0d4a2ac')" \
    && chmod -R a+rX /models

COPY model_server.py service.py start.sh ./

# Hugging Face Spaces run as UID 1000 with a read-only-ish filesystem; the baked
# weights are world-readable and nothing below writes outside /tmp.
USER 1000
ENV PORT=7860 \
    GLM_OCR_PORT=8080 \
    GLM_OCR_DTYPE=bfloat16 \
    GLM_OCR_MAX_PAGES=20 \
    HF_HUB_OFFLINE=1 \
    HOME=/tmp \
    GLM_OCR_THREADS=2 \
    OMP_NUM_THREADS=2

# GLM_OCR_API_KEY is a secret: set it in the Space settings and mirror it into
# the backend's WISEAU_GLM_OCR_API_KEY. Never bake it into the image.

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=5).status == 200 else 1)"

CMD ["/app/start.sh"]
