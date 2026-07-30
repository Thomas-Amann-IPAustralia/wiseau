---
title: wiseau docling converter
emoji: 📄
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# docling-serve — the internal converter (HF Space #2)

The high-fidelity half of [wiseau](../README.md). The backend (Space #1) sends
document bytes here and gets faithful Markdown back; if this Space is asleep,
slow, or down, the backend **falls back** to its deterministic PyMuPDF/Mammoth
parsers instead of failing (ADR-014). Nothing here is called by the public.

* Image: `ghcr.io/docling-project/docling-serve-cpu`, tag- and digest-pinned in
  the [`Dockerfile`](Dockerfile). Model weights are baked in — no download at
  boot.
* API: `POST /v1/convert/file` (multipart `files`, `to_formats=md`), health at
  `/health`. Only the convert endpoint is used by the backend.
* Design: [ADR-013/014/015](../docs/decisions.md), topology in
  [`../docs/tech-spec.md`](../docs/tech-spec.md) §8, engine selection in §11.

## Run it locally

```bash
docker build -t wiseau-docling docling/
docker run -p 7860:7860 -e DOCLING_SERVE_API_KEY=dev-key wiseau-docling
curl localhost:7860/health
```

Then point a local backend at it and the whole docling path is live. docling is
not the default engine (ADR-027), so also select it — either for the process
(`WISEAU_PDF_ENGINE=docling`, as below) or per request (`engine=docling`):

```bash
export WISEAU_DOCLING_BASE=http://localhost:7860
export WISEAU_DOCLING_API_KEY=dev-key
export WISEAU_PDF_ENGINE=docling      # or send engine=docling per conversion
uvicorn main:app --port 7860   # from backend/, on another port
```

> The image is ~4.4 GB and CPU inference is slow — a first conversion of a
> multi-page scanned PDF can take minutes. That is expected, and is exactly why
> the backend's timeout is generous and its fallback automatic.

## Deploying as a Hugging Face Space

1. Create a **Docker** Space (16 GB CPU tier) and push `docling/` to it — the
   frontmatter at the top of this file is the Space card, and `app_port: 7860`
   matches the `Dockerfile`.
2. Make the Space **private**, and in its settings add the secret
   `DOCLING_SERVE_API_KEY` (any long random string).
3. On the backend Space (#1), set:

   | Variable | Value |
   | -------- | ----- |
   | `WISEAU_DOCLING_BASE` | `https://<user>-<space>.hf.space` |
   | `WISEAU_DOCLING_API_KEY` | the same secret as above (sent as `X-Api-Key`) |
   | `WISEAU_DOCLING_TOKEN` | an HF access token with read access to the private Space (sent as `Authorization: Bearer`) |

   The two credentials guard different layers — the platform gateway and
   docling-serve itself — and are independent; set whichever the deployment uses.
   Add `WISEAU_PDF_ENGINE=docling` only if docling should be Space #1's *default*
   engine; without it, docling serves the callers who ask for it by name
   (`engine=docling`, which the UI's *Highest fidelity* option sends) — ADR-027.
4. Verify, in order: `/health` responds; a table-heavy PDF upload through the
   backend **with `engine=docling`** returns docling-quality Markdown; then
   **pause this Space** and repeat the same upload — it must still succeed, via
   the PyMuPDF fallback, with a `docling conversion failed ... falling back` line
   in the backend log.

## Sizing

The free 2 vCPU / 16 GB box fits **one** worker holding **one** copy of the
models (`DOCLING_SERVE_ENG_LOC_NUM_WORKERS=1`, `..._SHARE_MODELS=true`); a second
worker means a second model copy and an OOM kill instead of a queue. Concurrency
is already bounded upstream by the backend's `MAX_CONCURRENT_JOBS`, so requests
queue there rather than piling up here. `DOCLING_SERVE_MAX_SYNC_WAIT=100` keeps
docling's own 504 *ahead* of the backend's `WISEAU_DOCLING_TIMEOUT` (120s), so a
too-slow conversion becomes a clean fallback rather than a dangling socket.
