# Technical Specification — Universal Markdown Ingestion Engine

The detailed **"how"** to the project brief's **"why"**. Where the brief
([`project-brief.md`](project-brief.md)) sets vision and scope, this document is
the working contract: exact API shapes, module responsibilities, configuration,
error semantics, and the invariants every change must preserve.

If code and this spec disagree, that is a bug in one of them — reconcile them and
note it in [`decisions.md`](decisions.md).

---

## 1. Invariants (do not break these)

1. **Determinism (scoped — see ADR-013).** Fidelity now outranks strict
   reproducibility for the default document path. The paths that *are*
   deterministic stay so — `clean_markdown()` normalization, the PyMuPDF/Mammoth
   fallback parsers, and Trafilatura URL extraction all yield byte-identical
   output for a fixed input (no timestamps, no random ordering, no
   wall-clock-dependent content). The **docling** engine (default document
   parser, ADR-014) is ML-based and **best-effort**: its Markdown may vary
   run-to-run, and that is intended, not a bug. Do not "fix" it. (Live web pages
   also legitimately change; that is content drift, not a determinism violation —
   see §7.)
2. **Single contract.** Humans and agents receive the same `MarkdownResponse`
   JSON. There is no consumer-specific response shape.
3. **Normalization is universal.** Every Markdown-producing path ends in
   `cleaner.clean_markdown()`.
4. **Fair-use guards are unconditional.** Every heavy conversion runs inside the
   concurrency semaphore and under the rate limiter.
5. **Layer decoupling.** Frontend ↔ backend communicate only over HTTP; the
   frontend's only backend knowledge is `MARKDOWN_API_BASE`.

---

## 2. API contract

Base URL is the deployed backend (Hugging Face Space). All responses are JSON.
The machine-readable schema is FastAPI's auto-generated `/openapi.json`; the
interactive docs are at `/docs`.

### `GET /ping`
- **Purpose:** liveness/readiness for the UI status badge and background monitors.
- **Rate limit:** exempt.
- **200 response:**
  ```json
  { "status": "ok", "service": "markdown-ingestion-engine", "version": "0.1.0" }
  ```

### `POST /convert/url`
- **Purpose:** render a URL (JS-aware) and extract primary content as Markdown.
- **Rate limit:** `20/minute` per IP.
- **Request body:**
  ```json
  { "url": "https://example.com/article" }
  ```
  `url` is validated as an `HttpUrl`.
- **200 response:** `MarkdownResponse` (see §3).
- **Errors:** `422` invalid URL (FastAPI validation); `429` rate limited;
  `502` extraction/render failure.

### `POST /convert/file`
- **Purpose:** parse an uploaded PDF, DOCX, or image into Markdown. Scanned /
  handwritten PDFs and image uploads are OCR'd automatically (see §10).
- **Rate limit:** `20/minute` per IP.
- **Request:** `multipart/form-data` with a single `file` field.
- **Constraints:** extension must be `.pdf`, `.docx`, or an image type
  (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`, `.gif`); body must
  be non-empty and ≤ `MAX_UPLOAD_BYTES` (default 25 MB).
- **200 response:** `MarkdownResponse`.
- **Errors:** `400` empty upload; `413` too large; `415` unsupported type;
  `429` rate limited; `502` parse failure.

---

## 3. Data model

`MarkdownResponse` (returned by both conversion endpoints):

| Field      | Type   | Meaning                                             |
| ---------- | ------ | --------------------------------------------------- |
| `source`   | string | The URL or original filename that was converted.    |
| `markdown` | string | The cleaned, normalized Markdown.                   |
| `length`   | int    | `len(markdown)` — a convenience for clients.        |

Keep this shape additive: new fields may be appended, but existing fields must
not change type or meaning without a version bump (§6).

---

## 4. Module responsibilities

The backend is deliberately small and layered. Each module has one job.

| Module | Responsibility | Must not |
| ------ | -------------- | -------- |
| `main.py` | HTTP surface: routing, validation, CORS, rate limiting, concurrency ceiling, upload limits, error → HTTP mapping. | Contain extraction logic. |
| `parsers/__init__.py` | Public entrypoints: `url_to_markdown`, `file_to_markdown`. | — |
| `parsers/browser.py` | Build a stealth headless Chrome driver; download a URL's raw bytes *through that driver's session* (`fetch_bytes`), so WAF clearance/cookies carry over. | Know about Markdown; raise on a failed download (return `None`). |
| `parsers/url_parser.py` | Render → Trafilatura extract → (markdownify fallback) → clean. Detect a direct-PDF response and route its bytes to the document pipeline instead. | Contain per-site CSS selectors; trust a `.pdf` URL without verifying the magic bytes. |
| `parsers/file_parser.py` | Select the conversion engine (`WISEAU_PDF_ENGINE`): docling-first with automatic fallback to PyMuPDF4LLM (legacy mode) + per-page OCR / Mammoth. Dispatch by extension; then clean. | Hard-depend on docling; return unnormalized text; use PyMuPDF4LLM's unstable layout/OCR engine in the fallback. |
| `parsers/docling_client.py` *(Phase 6)* | Thin HTTP client to docling-serve (`WISEAU_DOCLING_BASE`): document bytes → Markdown. Bounded timeout; typed errors so the caller can tell "docling down" from "bad document". | Contain conversion logic itself; retry forever; leak the token. |
| `parsers/ocr.py` | Pluggable OCR engines (default MuPDF-Tesseract, opt-in EasyOCR): page image → text. Used by the *fallback* PDF path. | Introduce nondeterminism. |
| `parsers/cleaner.py` | Deterministic Unicode/whitespace/typography normalization. | Introduce nondeterminism. |

### Extraction pipelines

**URL (HTML):** `initialize_driver()` renders the page (45s load timeout) →
`page_source` → `trafilatura.extract(..., output_format="markdown",
favor_precision=True)` → if empty, `markdownify(html, heading_style="ATX")` →
`clean_markdown()`.

**URL (direct PDF):** if the rendered DOM is Chrome's PDF viewer (`<embed
type="application/pdf">`) *or* the URL path ends in `.pdf`, `browser.fetch_bytes`
downloads the URL from inside the already-navigated page (so the session's
cookies/WAF clearance apply). The bytes are accepted only if they start with
`%PDF-`; then they go through **`file_to_markdown`** — the same
docling-first-with-fallback document pipeline as an upload — under a filename
derived from the URL path. Bytes that aren't a PDF fall through to the HTML path;
an unmistakable viewer whose bytes are unreachable raises (→ 502) rather than
return the empty viewer shell. See ADR-017.

**PDF:** `pymupdf.open(stream=...)` → per-page: native pages via
`pymupdf4llm.to_markdown` (legacy mode), scanned pages via the OCR engine (§10) →
assemble in page order → `clean_markdown()`. A fully digital PDF keeps the single
whole-document `to_markdown(doc)` fast path.

**DOCX:** `mammoth.convert_to_html(...)` → `markdownify(..., heading_style="ATX")`
→ `clean_markdown()`.

**Image** (`.png`/`.jpg`/...): re-wrap as a one-page PDF → OCR engine (§10) →
`clean_markdown()`.

### `clean_markdown()` guarantees
Given identical input it returns identical output: NFC Unicode normalization,
CRLF/CR → LF, smart-quotes/dashes/ellipsis/nbsp/zero-width/BOM → plain ASCII,
trailing whitespace stripped, runs of ≥3 blank lines collapsed to one blank
line, exactly one trailing newline.

---

## 5. Configuration

All backend configuration is via environment variables (12-factor).

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `PORT` | `7860` | Listen port (matches Hugging Face Spaces). |
| `MAX_CONCURRENT_JOBS` | `4` | Global concurrency ceiling for heavy jobs. |
| `MAX_UPLOAD_BYTES` | `26214400` | Upload size limit (25 MB). |
| `CHROME_BIN` | — | Path to Chromium binary (set in Docker image). |
| `CHROMEDRIVER_PATH` | — | Path to chromedriver (set in Docker image). |
| `WISEAU_OCR_MODE` | `auto` | `auto` (OCR pages that need it), `force` (OCR every page), or `off` (native text only). |
| `WISEAU_OCR_ENGINE` | `tesseract` | OCR backend: `tesseract` (default) or `easyocr` (opt-in neural, handwriting). |
| `WISEAU_OCR_DPI` | `300` | Rasterization DPI for OCR (fixed for reproducibility). |
| `WISEAU_OCR_LANG` | `eng` | OCR language(s); Tesseract 639-2/T code(s), `+`-joined. |
| `WISEAU_PDF_ENGINE` | `docling` | *(Phase 6)* Document engine: `docling` (default; via docling-serve) or `pymupdf` (force the local fallback). |
| `WISEAU_DOCLING_BASE` | — | *(Phase 6)* Base URL of the internal docling-serve service (HF Space #2). Unset ⇒ behave as `pymupdf`. |
| `WISEAU_DOCLING_TOKEN` | — | *(Phase 6)* Sent as `Authorization: Bearer` — the *platform gateway* credential (an HF token when Space #2 is private). |
| `WISEAU_DOCLING_API_KEY` | — | *(Phase 6)* Sent as `X-Api-Key` — docling-serve's *own* guard, matching its `DOCLING_SERVE_API_KEY`. A different mechanism from the bearer token; either, both, or neither may be in use (ADR-018). |
| `WISEAU_DOCLING_TIMEOUT` | `120` | *(Phase 6)* Seconds to wait on docling before falling back (generous, to absorb cold starts). |

Rate limits are code-level constants in `main.py` (`60/min` + `1000/day` default;
`20/min` on convert routes). Promote them to env vars only if a real tuning need
arises — record the change in `decisions.md`.

Frontend configuration is the single `window.MARKDOWN_API_BASE` in
`frontend/config.js`.

---

## 6. Versioning & change control

- The API `version` lives in `main.py` (`FastAPI(version=...)`) and is echoed by
  `/ping`. Bump it when the contract changes.
- **Additive** changes (new optional field, new endpoint) → patch/minor bump.
- **Breaking** changes (removed/renamed field, changed error semantics) → major
  bump **and** an ADR in `decisions.md` explaining why.
- Update this spec in the same commit as any contract change.

---

## 7. Error handling & edge cases

- **Render/extraction failure** (dead URL, timeout, driver crash) → `502` with a
  clean `detail` message; the exception is logged server-side, never leaked as a
  stack trace to the caller.
- **Unsupported file type** → `415`; **oversized** → `413`; **empty** → `400`.
- **Rate limit** → `429` (handled by `slowapi`); **concurrency overflow** →
  requests *queue* on the semaphore rather than erroring.
- **Content drift:** monitoring/diff pipelines must treat legitimate page changes
  as expected. Determinism is per-input, not across time.

---

## 8. Deployment topology

```
GitHub Pages ──HTTPS──► HF Space #1: FastAPI + Chromium ──HTTP──► HF Space #2: docling-serve
 static frontend         backend, WAF-bypass, guards               PyTorch converter
                         16 GB / 2 vCPU                            16 GB / 2 vCPU  (Phase 6)
```

- Backend image version-locks Chromium + Python via the `Dockerfile`; runs as
  non-root UID 1000 (Hugging Face requirement) on port 7860.
- Frontend is served as static files; the only per-deployment edit is
  `config.js` → `MARKDOWN_API_BASE` pointing at the Space #1 URL.
- **docling-serve (Phase 6, ADR-015/018)** runs as a *second* HF Space, called
  only by the backend over `WISEAU_DOCLING_BASE` and not exposed to the public.
  Its image (`docling/Dockerfile`) is the upstream `docling-serve-cpu`, tag- and
  digest-pinned, with the model weights already baked in; re-homed for Spaces as
  UID 1000 on port 7860, one worker sharing one copy of the models, writable
  state under `/tmp`. Two independent guards, both optional and both supplied at
  deploy time: a private Space (bearer `WISEAU_DOCLING_TOKEN`) and docling-serve's
  own `DOCLING_SERVE_API_KEY` (`WISEAU_DOCLING_API_KEY`). Deployment steps are in
  [`docling/README.md`](../docling/README.md).
- Cold-start is mitigated by the Space's long inactivity timeout; docling
  cold-starts are additionally absorbed by `WISEAU_DOCLING_TIMEOUT` + the automatic
  fallback (a cold docling Space yields the PyMuPDF result, not an error).

---

## 9. Agent integration (Phase 4)

The agent-facing surface is documented in full in [`mcp.md`](mcp.md). Summary of
the contract-level guarantees:

- **MCP server** (`backend/mcp_server.py`) wraps `/convert/url`, `/convert/file`,
  and `/ping` as MCP tools (`convert_url`, `convert_file`, `ping`). It is a thin
  HTTP adapter over the running backend — every tool call is an HTTP request, so
  the tools reuse the exact `MarkdownResponse` shape and inherit the rate limiter
  and concurrency ceiling unchanged (invariant #4). No in-process bypass. Its
  only backend coupling is `WISEAU_API_BASE`, mirroring the frontend's
  `MARKDOWN_API_BASE` (ADR-009).
- **OpenAPI** is emitted at `/openapi.json` with explicit, clean operation IDs
  (`convert_url`, `convert_file`, `ping`) and per-route summaries so the schema
  reads well as a function-calling tool definition. Setting operation IDs is a
  fixed part of the contract now — do not let them regress to FastAPI's
  auto-generated `*_post` names.
- **Autonomous ingestion** (`backend/monitor.py`) consumes the same
  route (`POST /convert/url`) as a thin, stdlib-only HTTP client, so it inherits
  the same guards. It snapshots each URL's Markdown and diffs fresh conversions
  against the last, treating content drift as an expected `changed` outcome rather
  than an error (§7); only a failure to reach/render is an `error`. See
  [`mcp.md`](mcp.md) §3 and ADR-010.

See [`roadmap.md`](roadmap.md) for the task breakdown and [`mcp.md`](mcp.md) for
client wiring.

---

## 10. OCR (scanned & handwritten documents)

Born-digital PDFs carry a text layer that is read directly. Scanned and
handwritten PDFs are page *images* with no text layer, so they are OCR'd. Image
uploads are OCR'd the same way. This is handled in `parsers/file_parser.py` +
`parsers/ocr.py`; see **ADR-012** for the full rationale.

**Detection & assembly.** Detection is per page: a page with `< 16` non-whitespace
characters of embedded text is treated as image-only and OCR'd; other pages take
the fast native path. The document is then assembled in page order. A fully
digital PDF keeps the exact pre-OCR fast path (`pymupdf4llm.to_markdown(doc)`); a
fully scanned PDF is entirely OCR'd; a mixed PDF interleaves.

**Determinism (invariant #1).** Two deliberate choices keep output byte-stable:
1. `pymupdf4llm.use_layout(False)` — the 1.28 layout engine accumulates cross-call
   state that non-deterministically drops content; the legacy extractor is stable.
2. OCR uses MuPDF's own primitive (`Page.get_textpage_ocr`), **not**
   PyMuPDF4LLM's OCR integration, which has the same instability.
OCR DPI is a fixed constant (300). All OCR output still ends in `clean_markdown()`
(invariant #3) and runs under the concurrency + rate-limit guards (invariant #4).

**Engines (pluggable, `parsers/ocr.py`).**
- `tesseract` (default) — MuPDF's built-in Tesseract. System binary only (no extra
  Python dependency); deterministic; strong on printed/scanned text; weak on
  cursive handwriting. Installed in the Docker image (`tesseract-ocr` +
  `tesseract-ocr-eng`); tessdata is auto-discovered (no `TESSDATA_PREFIX` needed).
- `easyocr` (opt-in) — a neural engine that handles handwriting and noisy
  captures. Enabled with `WISEAU_OCR_ENGINE=easyocr` after
  `pip install -r requirements-ocr.txt`. PyTorch is heavy, so it is kept out of
  the default image; determinism holds for fixed model weights on CPU.

For a predominantly handwritten corpus, EasyOCR is the recommended engine; a
dedicated handwriting model (e.g. TrOCR) could be added as a further engine
behind the same `OcrEngine` interface. Configuration: see §5
(`WISEAU_OCR_MODE`/`ENGINE`/`DPI`/`LANG`).

---

## 11. Extraction engine selection & fallback (Phase 6)

> **Status: the whole backend half is implemented & unit-tested (ADR-014/016/017)
> — the docling client, engine-selection/fallback in `file_parser.py`, and
> direct-PDF `/convert/url` routing, all covered by a mocked-transport /
> faked-driver suite. The docling Space image is written and digest-pinned
> (ADR-018) but has never been built or deployed, so nothing below has been
> verified against a live docling-serve.**

Document conversion — `/convert/file`, and `/convert/url` when the URL serves a
PDF (ADR-017) — is **docling-first with automatic fallback**:

1. `file_parser.py` reads `WISEAU_PDF_ENGINE` (default `docling`).
2. If `docling` **and** `WISEAU_DOCLING_BASE` is set: `docling_client.py` POSTs the
   document bytes to `POST /v1/convert/file` on docling-serve (credentials as in
   §5), requesting `md` output, within `WISEAU_DOCLING_TIMEOUT`.
3. **Fall back** to the local parser (PyMuPDF4LLM + OCR for PDF/image; Mammoth for
   DOCX) whenever docling is unset/`pymupdf`, times out, returns a 5xx or
   connection error, returns empty/non-JSON, refuses the *request* (401/403/429),
   or **rejects the document** with another 4xx. The client raises typed errors so
   the two cases are logged apart — `DoclingUnavailable` (infrastructure:
   down/asleep/timeout/5xx/empty, plus auth/rate-limit refusals, which say nothing
   about the document — ADR-018) vs `DoclingBadDocument` (a 4xx verdict on the
   input) — both subclass `DoclingError`, which `file_parser` catches to fall
   back. Every fallback is
   logged so silent docling outages are visible (feeds the observability backlog).
   The client is stdlib-`urllib`, not `httpx`, so the backend image gains no new
   runtime dependency and cannot fail to import if an HTTP library is absent
   (ADR-016).
4. Whichever engine answers, the result flows through `clean_markdown()`
   (invariant #3) inside the `_job_semaphore` (invariant #4). The public
   `MarkdownResponse` shape is **unchanged** — this is an engine swap behind the
   contract, so no response-shape version bump. (Which engine served a request is
   logged for observability; it is not part of the contract.)

DOCX stays on Mammoth by default (cheap, deterministic); route it to docling only
when `WISEAU_PDF_ENGINE=docling` is explicitly set *and* docling is reachable.

Determinism note (ADR-013): the docling path is best-effort and may vary
run-to-run; the fallback path is deterministic. So the *same* document can yield
different Markdown depending on which engine served it — intended, not a bug.
