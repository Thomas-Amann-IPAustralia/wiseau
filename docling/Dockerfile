# docling-serve — HF Space #2, the internal high-fidelity converter (ADR-015).
#
# This is the *second* of the project's two heavy boxes: the wiseau backend (with
# headless Chrome) is Space #1 and calls this one over HTTP at
# WISEAU_DOCLING_BASE. Nothing else should reach it — see README.md for the two
# guards (private Space + DOCLING_SERVE_API_KEY).
#
# Built on the upstream **CPU** image, which already ships the model weights, so
# the Space needs no model download at boot (a cold start that pulled ~500 MB of
# weights would blow past the backend's fallback timeout on every wake). The tag
# is pinned *and* digest-locked: docling output is stochastic by design (ADR-013),
# so pinning the engine is what keeps that variation bounded to the model, not to
# whatever "latest" happened to be that day.
#
# Build/run locally (the image is ~4.4 GB, so expect a slow first pull):
#   docker build -t wiseau-docling docling/
#   docker run -p 7860:7860 -e DOCLING_SERVE_API_KEY=dev-key wiseau-docling
#   curl localhost:7860/health
FROM ghcr.io/docling-project/docling-serve-cpu:v1.27.0@sha256:a70cd391c5d9e7ad23e8d76adffa5e5f03c536b4d1ea007f512c94e0b435ba45

# Hugging Face Spaces run the container as UID 1000. The upstream image is built
# for UID 1001 with group-0 permissions (OpenShift convention), so the baked-in
# model weights are already readable by any UID — but everything this process
# *writes* must live somewhere world-writable, hence the /tmp paths below.
USER 0
RUN mkdir -p /tmp/docling-scratch /tmp/docling-home && chmod 1777 /tmp/docling-scratch /tmp/docling-home
USER 1000

ENV \
    # Serve on the port Spaces expects, on all interfaces.
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=7860 \
    # One worker process; concurrency is capped by the engine setting below.
    UVICORN_WORKERS=1 \
    # Writable state for a non-root, read-only-ish filesystem.
    HOME=/tmp/docling-home \
    HF_HOME=/tmp/docling-home/huggingface \
    DOCLING_SERVE_SCRATCH_PATH=/tmp/docling-scratch \
    # 2 vCPU / 16 GB free tier: a single worker holding one copy of the models.
    # More workers means more model copies and an OOM kill instead of a queue —
    # the backend already serializes its own load behind MAX_CONCURRENT_JOBS.
    DOCLING_SERVE_ENG_LOC_NUM_WORKERS=1 \
    DOCLING_SERVE_ENG_LOC_SHARE_MODELS=true \
    OMP_NUM_THREADS=2 \
    # Load the models at boot so the first real request isn't also the one that
    # pays for warm-up (it would exceed WISEAU_DOCLING_TIMEOUT and fall back).
    DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true \
    DOCLING_SERVE_OPTIONS_CACHE_SIZE=1 \
    # Answer 504 *before* the backend's 120s client timeout expires, so a slow
    # conversion degrades as a clean fallback rather than a dangling socket.
    DOCLING_SERVE_MAX_SYNC_WAIT=100 \
    # Bound the work a single request can ask for on shared free compute.
    DOCLING_SERVE_MAX_NUM_PAGES=500 \
    DOCLING_SERVE_MAX_FILE_SIZE=26214400 \
    # No demo UI, no memory endpoints: this Space has exactly one client.
    DOCLING_SERVE_ENABLE_UI=false \
    DOCLING_SERVE_ENABLE_MANAGEMENT_ENDPOINTS=false \
    # Never let a document's contents trigger an outbound call from this box.
    DOCLING_SERVE_ENABLE_REMOTE_SERVICES=false \
    DOCLING_SERVE_ALLOW_EXTERNAL_PLUGINS=false \
    DOCLING_SERVE_LOG_LEVEL=INFO

# DOCLING_SERVE_API_KEY is deliberately NOT set here — it is a secret, supplied
# at deploy time (Space settings) and mirrored into the backend's
# WISEAU_DOCLING_API_KEY. Baking a shared secret into a public image would
# publish it.

EXPOSE 7860

# /health is what the backend's warm-ping (and any uptime check) should poll.
HEALTHCHECK --interval=60s --timeout=10s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=5).status == 200 else 1)"

CMD ["docling-serve", "run"]
