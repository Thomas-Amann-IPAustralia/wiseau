---
title: wiseau GLM-OCR converter
emoji: 🔎
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# GLM-OCR — the optional third engine (`engine="glm-ocr"`)

An opt-in document engine for [wiseau](../README.md), next to the fast local
parser and docling (ADR-034). The backend sends document bytes here and gets
Markdown back; if this service is asleep, slow, refuses the document, or is not
deployed at all, the backend **falls back** to its deterministic parser instead
of failing (ADR-014). Nothing here is called by the public.

* **What runs:** the official [GLM-OCR SDK](https://github.com/zai-org/GLM-OCR)
  pipeline — PDF rasterized at 200 DPI, `PP-DocLayoutV3` layout detection, then
  each detected region transcribed by the 0.9B
  [`zai-org/GLM-OCR`](https://huggingface.co/zai-org/GLM-OCR) model — with the
  model served by `transformers` on CPU (`model_server.py`) and the pipeline
  behind one endpoint (`service.py`). Weights for both models are baked into the
  image at pinned revisions.
* **API:** `POST /v1/convert/file` (multipart, one file under `files`) →
  `{"markdown", "pages", "duration_ms"}`; health at `/health`.
* **Reads:** PDFs and images. **Not** DOCX — the backend sends DOCX to Mammoth.
* **Design and measurements:** [ADR-034](../docs/decisions.md) and the bake-off
  report in [`docs/benchmarks/`](../docs/benchmarks/).

## When to use it — and when not

GLM-OCR **re-reads every page from pixels**. That is its strength on scans (no
text layer to read) and its weakness on born-digital PDFs (it discards a text
layer that is already exact). It is also a *generative* model: it can produce a
plausible word that is not on the page. Read the bake-off before making it a
deployment default.

On CPU it is **slow — minutes per page**, measured on a 4-vCPU box in the
bake-off. The service therefore refuses PDFs longer than `GLM_OCR_MAX_PAGES`
(default 20) with a 413, which the backend answers instantly with its fast
parser, and the backend's `WISEAU_GLM_OCR_TIMEOUT` defaults to 300 s. On GPU
hardware, replace `model_server.py` with vLLM or SGLang serving the same model
on port 8080; `service.py` and the backend are unchanged.

## Run it locally

```bash
docker build -t wiseau-glm-ocr glm-ocr/
docker run -p 7860:7860 -e GLM_OCR_API_KEY=dev-key wiseau-glm-ocr
curl localhost:7860/health
```

Then point a local backend at it and ask for the engine per request:

```bash
export WISEAU_GLM_OCR_BASE=http://localhost:7860
export WISEAU_GLM_OCR_API_KEY=dev-key
uvicorn main:app --port 7861   # from backend/
curl -F file=@scan.pdf -F engine=glm-ocr localhost:7861/convert/file
```

Without Docker (what the bake-off did): install `requirements.txt` plus CPU
`torch`/`torchvision`, start `python model_server.py`, wait for
`localhost:8080/health`, then `python service.py`.

## Deploying as a Hugging Face Space

1. Create a **Docker** Space (16 GB CPU tier) and push `glm-ocr/` to it — the
   frontmatter above is the Space card.
2. Make it **private** and add the secret `GLM_OCR_API_KEY`.
3. On the backend Space set `WISEAU_GLM_OCR_BASE`, `WISEAU_GLM_OCR_API_KEY`
   (same secret, sent as `X-Api-Key`) and `WISEAU_GLM_OCR_TOKEN` (an HF token
   that can read the private Space). `/ping` then lists `glm-ocr` in
   `available_engines`, and the UI shows its option.
4. Verify: `/health` responds; a short scanned PDF uploaded through the backend
   with `engine=glm-ocr` comes back with `glm-ocr` counted under
   `GET /metrics` → `engines`; then pause this Space and repeat — the upload must
   still succeed via the fallback, with `glm_ocr.fallbacks` incremented.

## Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `GLM_OCR_API_KEY` | unset | Required `X-Api-Key` when set. |
| `GLM_OCR_MAX_PAGES` | `20` | Longer PDFs get a 413 (the backend falls back at once). |
| `GLM_OCR_MAX_FILE_MB` | `25` | Upload ceiling. |
| `GLM_OCR_PDF_DPI` | `200` | Rasterization DPI (the SDK's default). |
| `GLM_OCR_REGION_TIMEOUT` | `1800` | Seconds the pipeline waits on one region. |
| `GLM_OCR_DTYPE` | `bfloat16` | `float32` on CPUs without fast bf16 (no AVX512-BF16/AMX). |
| `GLM_OCR_THREADS` | `2` | Torch threads for the model. |
| `GLM_OCR_MAX_NEW_TOKENS` | `4096` | Cap per region — bounds a runaway (repeating) generation. |
