# Technical Specification — Universal Markdown Ingestion Engine

The detailed **"how"** to the project brief's **"why"**. Where the brief
([`project-brief.md`](project-brief.md)) sets vision and scope, this document is
the working contract: exact API shapes, module responsibilities, configuration,
error semantics, and the invariants every change must preserve.

If code and this spec disagree, that is a bug in one of them — reconcile them and
note it in [`decisions.md`](decisions.md).

---

## 1. Invariants (do not break these)

1. **Determinism (scoped — see ADR-013, amended by ADR-027).** Fidelity outranks
   strict reproducibility *where fidelity is asked for*. The default paths are
   deterministic and stay so — `clean_markdown()` normalization, the
   PyMuPDF/Mammoth parsers (the **default** document engine, ADR-027), and
   Trafilatura URL extraction all yield byte-identical output for a fixed input
   (no timestamps, no random ordering, no wall-clock-dependent content). The
   **docling** engine (ADR-014; selected per deployment or per request) is
   ML-based and **best-effort**: its Markdown may vary run-to-run, and that is
   intended, not a bug. Do not "fix" it. (Live web pages also legitimately
   change; that is content drift, not a determinism violation — see §7.)
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
- **Rate limit:** exempt (`@limiter.exempt`, honoured by `SlowAPIMiddleware`).
- **200 response:**
  ```json
  {
    "status": "ok",
    "service": "markdown-ingestion-engine",
    "version": "0.7.0",
    "engines": ["docling", "pymupdf"],
    "default_engine": "pymupdf"
  }
  ```
  `engines` lists the names a caller may pass as `engine` on a convert request
  (plus the always-accepted `auto`), so a client can offer the choice without
  hard-coding it. `default_engine` is the one `auto` resolves to *here* — i.e.
  this deployment's `WISEAU_PDF_ENGINE`, reported as the engine that will
  actually run (anything but `docling` runs the local parser). A client uses it
  to label its "auto" option and size a progress estimate (ADR-027).

### `GET /metrics`
- **Purpose:** operational counters for *this process* — request/job timings,
  peak concurrency, memory, and which extraction engine served each conversion
  (see §12). Not part of the conversion contract; shape may change without a
  major bump.
- **Rate limit:** the default (`60/min`, `1000/day`), applied by
  `SlowAPIMiddleware` because it carries no explicit `@limiter.limit`. It does no
  heavy work, so it takes no job slot.
- **200 response:** a JSON object; see §12 for the fields.

### `POST /convert/url`
- **Purpose:** render a URL (JS-aware) and extract primary content as Markdown.
- **Rate limit:** `20/minute` per IP.
- **Request body:**
  ```json
  { "url": "https://example.com/article", "engine": "auto" }
  ```
  `url` is validated as an `HttpUrl`. `engine` is optional (see the box below);
  on this endpoint it applies only when the URL turns out to serve a **PDF** —
  an HTML page is extracted by Trafilatura regardless.
- **200 response:** `MarkdownResponse` (see §3).
- **Errors:** `400` the URL resolves to a non-public address and this
  deployment refuses to fetch it (§13), or `engine` names an engine this build
  cannot run; `422` invalid URL (FastAPI validation); `429` rate limited;
  `502` extraction/render failure.

### `POST /convert/file`
- **Purpose:** parse an uploaded PDF, DOCX, or image into Markdown. Scanned /
  handwritten PDFs and image uploads are OCR'd automatically (see §10).
- **Rate limit:** `20/minute` per IP.
- **Request:** `multipart/form-data` with a `file` field and an optional
  `engine` field (see the box below).
- **Constraints:** extension must be `.pdf`, `.docx`, or an image type
  (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`, `.gif`); body must
  be non-empty and ≤ `MAX_UPLOAD_BYTES` (default 25 MB). The limit is enforced
  while the part is *streamed*, so an oversized body is refused without being
  assembled in memory.
- **200 response:** `MarkdownResponse`.
- **Errors:** `400` empty upload or an unknown `engine`; `413` too large;
  `415` unsupported type; `429` rate limited; `502` parse failure.

> **The `engine` parameter (ADR-025).** Optional on both convert endpoints:
> `pymupdf` (fast and deterministic — what `WISEAU_PDF_ENGINE` defaults to,
> ADR-027), `docling` (highest fidelity, far slower on free CPU), or `auto` —
> the parameter's default — which defers to this deployment's
> `WISEAU_PDF_ENGINE`. `GET /ping` reports which engine that is
> (`default_engine`). An unknown value is a **400**, never a silent
> substitution. Requesting `docling` does **not** disable ADR-014's automatic
> fallback: if docling is unavailable the deterministic parser still answers, so
> choosing fidelity cannot cost resilience. The response does not report which
> engine actually ran; `GET /metrics` does (§12).

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
| `main.py` | HTTP surface: routing, validation, CORS, rate limiting (decorator limits **and** `SlowAPIMiddleware` for the defaults), concurrency ceiling, streamed upload limits, error → HTTP mapping, per-request timing/logging. | Contain extraction logic; read a whole upload before checking its size. |
| `observability.py` | Structured (JSON) log formatting and the in-process metrics registry read by `GET /metrics`. Imported by `main.py` *and* the parsers. | Affect extraction output in any way; add a runtime dependency; record URLs, filenames, or content into `/metrics`. |
| `parsers/__init__.py` | Public entrypoints: `url_to_markdown`, `file_to_markdown`, `resolve_engine`, `default_engine`. | — |
| `parsers/browser.py` | Build a stealth headless Chrome driver; download a URL's raw bytes *through that driver's session* (`fetch_bytes`), so WAF clearance/cookies carry over. | Know about Markdown; raise on a failed download (return `None`). |
| `parsers/url_parser.py` | Refuse non-public addresses (§13), then render → Trafilatura extract → (markdownify fallback) → clean. Detect a direct-PDF response and route its bytes to the document pipeline instead. | Contain per-site CSS selectors; trust a `.pdf` URL without verifying the magic bytes; start the browser before the address is vetted. |
| `parsers/file_parser.py` | Select the conversion engine (the request's `engine`, else `WISEAU_PDF_ENGINE`, default `pymupdf`): PyMuPDF4LLM (legacy mode) + per-page OCR / Mammoth, or docling when it is selected — with automatic fallback to the local parsers. Dispatch by extension; then clean. Validate a caller's engine choice (`resolve_engine`); report the deployment default (`default_engine`). | Hard-depend on docling; return unnormalized text; inline a DOCX image as a base64 data URI (ADR-024); use PyMuPDF4LLM's unstable layout/OCR engine in the fallback. |
| `parsers/docling_client.py` *(Phase 6)* | Thin HTTP client to docling-serve (`WISEAU_DOCLING_BASE`): document bytes → Markdown, images requested as placeholders (ADR-024). Bounded timeout; typed errors so the caller can tell "docling down" from "bad document". | Contain conversion logic itself; retry forever; leak the token. |
| `parsers/ocr.py` | Pluggable OCR engines (default MuPDF-Tesseract, opt-in EasyOCR): page image → text. Used by the *fallback* PDF path. | Introduce nondeterminism. |
| `parsers/cleaner.py` | Deterministic Unicode/whitespace/typography normalization; elide base64 data-URI payloads (ADR-024). | Introduce nondeterminism; remove content (it edits payloads, not text). |

### Extraction pipelines

**URL (HTML):** `initialize_driver()` renders the page (45s load timeout) →
`page_source` → `trafilatura.extract(..., output_format="markdown",
favor_precision=True)` → if empty, `markdownify(html, heading_style="ATX")` →
`clean_markdown()`. Trafilatura output additionally passes through
`_drop_repeated_run`, which removes the duplicated body Trafilatura emits for
pages under its 250-character threshold (ADR-020). It only drops an **exact,
adjacent** repeat of **40–250 characters** — the upper bound being Trafilatura's
own `MIN_EXTRACTED_SIZE`, so a repeat too large for the upstream bug to have
produced is left alone no matter how short the document (ADR-022). The block cap
bounds the scan's cost, not what it may touch.

**URL (direct PDF):** the address guard (§13) runs first for every URL. If the
rendered DOM is Chrome's PDF viewer (`<embed
type="application/pdf">`) *or* the URL path ends in `.pdf`, `browser.fetch_bytes`
downloads the URL from inside the already-navigated page (so the session's
cookies/WAF clearance apply). The bytes are accepted only if they start with
`%PDF-`; then they go through **`file_to_markdown`** — the same
engine-selected document pipeline as an upload — under a filename
derived from the URL path. Bytes that aren't a PDF fall through to the HTML path;
an unmistakable viewer whose bytes are unreachable raises (→ 502) rather than
return the empty viewer shell. See ADR-017.

**PDF:** `pymupdf.open(stream=...)` → per-page: native pages via
`pymupdf4llm.to_markdown` (legacy mode), scanned pages via the OCR engine (§10) →
assemble in page order → `clean_markdown()`. A fully digital PDF keeps the single
whole-document `to_markdown(doc)` fast path.

**DOCX:** `mammoth.convert_to_html(..., convert_image=<no payload>)` →
`markdownify(..., heading_style="ATX")` → `clean_markdown()`. Mammoth's *default*
image handler inlines every picture as a base64 data URI; ours emits the image
element without a source, so a screenshot cannot add tens of thousands of
unreadable characters to the output (ADR-024).

**Image** (`.png`/`.jpg`/...): re-wrap as a one-page PDF → OCR engine (§10) →
`clean_markdown()`.

### `clean_markdown()` guarantees
Given identical input it returns identical output: **base64 data-URI payloads
elided** (see below), NFC Unicode normalization, CRLF/CR → LF,
smart-quotes/dashes/ellipsis/nbsp/zero-width/BOM → plain ASCII, trailing
whitespace stripped, runs of ≥3 blank lines collapsed to one blank line, exactly
one trailing newline.

**Inlined images (ADR-024).** Any `data:<media-type>;base64,<payload>` becomes
`data:<media-type>;base64,...`, wherever it occurs — Markdown image syntax, an
HTML attribute, or bare text. So `![Figure 1](data:image/png;base64,iVBORw0…)`
survives as `![Figure 1](data:image/png;base64,...)`: the document still records
that a PNG was there, without carrying a blob that can be far larger than the
text around it. This is payload-only — no image, link, or paragraph is removed,
and there is no size threshold, so the rule stays deterministic. Sources are
fixed too (the DOCX handler above; `image_export_mode=placeholder` on docling
requests), and this is the backstop for anything else.

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
| `WISEAU_PDF_ENGINE` | `pymupdf` | **Default** document engine: `pymupdf` (the fast, deterministic local parser — ADR-027) or `docling` (via docling-serve, for fidelity). A request's `engine` field overrides it per conversion (ADR-025). |
| `WISEAU_DOCLING_BASE` | — | *(Phase 6)* Base URL of the internal docling-serve service (HF Space #2). Unset ⇒ docling is skipped even when it is selected. |
| `WISEAU_DOCLING_TOKEN` | — | *(Phase 6)* Sent as `Authorization: Bearer` — the *platform gateway* credential (an HF token when Space #2 is private). |
| `WISEAU_DOCLING_API_KEY` | — | *(Phase 6)* Sent as `X-Api-Key` — docling-serve's *own* guard, matching its `DOCLING_SERVE_API_KEY`. A different mechanism from the bearer token; either, both, or neither may be in use (ADR-018). |
| `WISEAU_DOCLING_TIMEOUT` | `120` | *(Phase 6)* Seconds to wait on docling before falling back (generous, to absorb cold starts). |
| `WISEAU_DOCLING_PATH` | `/v1/convert/file` | *(Phase 6)* docling-serve convert endpoint path; override only if a server version moves it. |
| `WISEAU_ALLOW_PRIVATE_URLS` | unset (off) | Allow `/convert/url` to fetch loopback/private/link-local addresses. Off by default (ADR-021); set to `1` for a self-hosted deployment that converts its own intranet. |
| `WISEAU_LOG_FORMAT` | `json` | Log rendering: `json` (one object per line, for aggregators) or `text` (human-readable, for local work). |
| `WISEAU_LOG_LEVEL` | `INFO` | Root log level. |

Rate limits are code-level constants in `main.py` (`60/min` + `1000/day` default;
`20/min` on convert routes). Promote them to env vars only if a real tuning need
arises — record the change in `decisions.md`.

> **Do not remove `SlowAPIMiddleware`.** slowapi enforces a route's decorator limit
> from the decorator, but the `default_limits` *only* from that middleware. Without
> it the defaults bind nothing, every undecorated route (`/metrics`,
> `/openapi.json`, `/docs`) goes unlimited, and `@limiter.exempt` stops meaning
> anything — silently, since the convert routes keep working. `test_api.py` pins
> all three behaviours.

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
- **Unsupported file type** → `415`; **oversized** → `413` (raised mid-stream, so
  the body is never fully buffered); **empty** → `400`.
- **Non-public target address** → `400` with the refusing detail (§13). A caller
  error, deliberately distinct from the `502` a genuine render failure gets.
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
  non-root UID 1000 (Hugging Face requirement) on port 7860. A Docker Space takes
  its configuration from YAML frontmatter in the Space repo's `README.md`, so
  `backend/README.md` (like `docling/README.md`) carries a Space card declaring
  `sdk: docker` and `app_port: 7860`; deploying means pushing the contents of
  `backend/` to the Space repo root.
- Frontend is served as static files; the only per-deployment edit is
  `config.js` → `MARKDOWN_API_BASE` pointing at the Space #1 URL. It is published
  by `.github/workflows/deploy-frontend.yml` rather than Pages' branch setting,
  which can only serve a repository root or `/docs` — and `/docs` is this
  documentation. That workflow rewrites the `MARKDOWN_API_BASE` assignment in the
  *uploaded* copy when the repository variable of the same name is set, so the
  deployed site can point at a Space with no commit and the committed default
  stays `localhost` (ADR-023).
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

> **Default (ADR-027):** the engine layer below is unchanged, but the *default*
> engine is now `pymupdf`, not `docling`. docling is selected per deployment
> (`WISEAU_PDF_ENGINE=docling`) or per request (`engine="docling"`).

> **Status: the whole backend half is implemented & unit-tested (ADR-014/016/017)
> — the docling client, engine-selection/fallback in `file_parser.py`, and
> direct-PDF `/convert/url` routing, all covered by a mocked-transport /
> faked-driver suite, plus a loopback HTTP stub that exercises the client's real
> `urllib` transport (endpoint, multipart body, both credentials) and the
> end-to-end fallback. The docling Space image is written and digest-pinned
> (ADR-018) but has never been built or deployed, so nothing below has been
> verified against *real* docling-serve — only against a stub that speaks its
> response shape.**

Document conversion — `/convert/file`, and `/convert/url` when the URL serves a
PDF (ADR-017) — runs the **fast local parser by default**, with docling
available on request and **automatic fallback** whenever docling is chosen:

1. `file_parser.py` takes the request's `engine` if it named one (ADR-025),
   otherwise `WISEAU_PDF_ENGINE` (default `pymupdf` — ADR-027). A caller's value
   is validated in `main.py` first — unknown ⇒ 400 — and `auto` resolves to "no
   opinion" rather than to a guessed engine name.
2. If `docling` **and** `WISEAU_DOCLING_BASE` is set: `docling_client.py` POSTs the
   document bytes to `POST /v1/convert/file` on docling-serve (credentials as in
   §5), requesting `md` output, within `WISEAU_DOCLING_TIMEOUT`.
3. **Fall back** to the local parser (PyMuPDF4LLM + OCR for PDF/image; Mammoth for
   DOCX) whenever docling is unset/`pymupdf` — including when the *request* asked
   for docling explicitly; fidelity is a preference, not a promise — times out, returns a 5xx or
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

DOCX takes the same engine selection as everything else: with `docling` selected
*and* a base configured, a DOCX goes to docling too, and falls back to Mammoth on
any failure. Mammoth serves it whenever docling is unselected (the default —
ADR-027), unset, or unreachable — which is the common case, and cheap and
deterministic when it happens.

Determinism note (ADR-013, as amended by ADR-027): the docling path is
best-effort and may vary run-to-run; the default local path is deterministic. So
the *same* document can yield different Markdown depending on which engine served
it — intended, not a bug — but a deployment that never selects docling is
deterministic end to end.
Which engine served a request is visible in `GET /metrics` (§12) — the only way
to tell a working docling from one that has been quietly falling back for weeks.

---

## 12. Observability (ADR-019)

A side channel, in both directions: nothing here may change a byte of extracted
Markdown, and nothing here is part of the conversion contract.

**Structured logs.** `observability.configure_logging()` installs a JSON-lines
formatter (`WISEAU_LOG_FORMAT=text` opts out). One object per record; callers add
fields with `extra={"wiseau": {...}}` rather than formatting them into the
message. Every request produces exactly one access line — uvicorn's own access
log is switched off in the `Dockerfile` CMD and in `main.__main__` so it does not
duplicate it — carrying `request_id`, `method`, `path`, `status`, `duration_ms`,
and `client`. The same `request_id` is returned to the caller as `X-Request-ID`
(exposed through CORS), so a user-reported problem can be found in the log.

**Metrics.** `GET /metrics` returns the process's counters:

| Group | Contents | Answers |
| ----- | -------- | ------- |
| `requests` | Count by **route template** (never an arbitrary caller-supplied path — a path is only accepted as a label when this app registers it, so nothing else can inflate the table; everything else buckets to `unmatched`) and by status; duration count/mean/p50/p95/max per route. A request the rate-limit middleware rejects never reaches the router, so it is attributed by that registered-path check rather than lost to `unmatched`. | Is anything erroring? |
| `jobs` | `in_flight`, `max_in_flight`, semaphore queue-wait and run-duration series. | What should `MAX_CONCURRENT_JOBS` be? Is anything queuing? |
| `conversions` | `url.ok` / `url.error` / `file.ok` / `file.error`. | Success rate per surface. |
| `engines` | Count per engine that actually produced Markdown: `docling`, `pymupdf`, `mammoth`, `ocr`, `trafilatura`, `markdownify`. Each parser records its own, so a PDF with **any** OCR'd page counts as `ocr` rather than `pymupdf` — one engine per conversion, and the OCR path stays visible. | **Is docling serving anything?** |
| `docling` | `attempts` / `successes` / `fallbacks` / `skipped`, `reasons` (`DoclingUnavailable`, `DoclingBadDocument`, `not_configured`, `engine_not_selected`), and call durations. | Is the Space down, misconfigured, or just slow? |
| `memory` | `peak_rss_mb` (getrusage) and `rss_mb` (Linux `/proc/self/statm`). | Headroom against the Space's limit. |

Constraints that keep it honest: **aggregates only** — no URLs, filenames, or
document content, because the endpoint is public. Counters are per-process and
reset on restart; percentiles come from a bounded window of recent samples, so
memory use is fixed. Recording is thread-safe, because the parsers record from
the worker threads `asyncio.to_thread` runs them in. Adding a Prometheus client
or an exporter was rejected (ADR-019): stdlib only, no new runtime dependency.

---

## 13. Fetch-target policy (ADR-021)

`/convert/url` is public, unauthenticated, and fetches the host it is given from
*inside* the container — so without a check it is a server-side request forgery
primitive. `url_parser.assert_url_allowed` runs before the browser starts and
refuses the request when:

- the scheme is not `http`/`https` (so `file:`, `ftp:`, `chrome:` are out — the API
  layer's `HttpUrl` also blocks these, but the parser is reachable from
  `monitor.py` and the tests), or
- **any** address the host resolves to is loopback, private, link-local, reserved,
  multicast or unspecified. Every record must be public: a name answering with both
  a public and a private address must not be a coin flip on which one Chrome picks.

Refusal is a `BlockedUrlError` → **400** with the reason. An unresolvable host is
*allowed* through, so DNS failure surfaces as an ordinary `502` render error rather
than a misleading `400`.

`WISEAU_ALLOW_PRIVATE_URLS=1` disables the address check (not the scheme check) for
a self-hosted deployment that converts its own intranet. The live-browser tests set
it, because their fixtures are served over loopback.

**Known limits — do not describe this as airtight.** Chrome follows redirects
itself, so a public URL that redirects to a private one still reaches it, and a DNS
rebind between the lookup and the render wins. Closing those requires a
proxy/egress control at the network layer, which is where it belongs. This closes
the direct case, which is the only one a caller can trivially aim.

---

## 14. Frontend behaviour (ADR-026)

The UI is still a static, dependency-free bundle (`index.html` + `style.css` +
`app.js` + `markdown.js` + `config.js` + `favicon.svg`) served straight from
GitHub Pages. No framework, no build step, no third-party script (ADR-004).

**Engine picker.** Three options — *Auto* / *Fastest (PyMuPDF / Mammoth)* /
*Highest fidelity (docling — slow)* — sent as the request's `engine` (§2).
*Fastest* is listed first because it is what a standard deployment defaults to
(ADR-027), and the panel says so; it also states plainly what docling costs
(tens of seconds to minutes on free CPU, longer on a cold Space) and that it
falls back automatically. `GET /ping` advertises both the accepted names and the
deployment's `default_engine`, so the list and the *Auto* label are read off the
backend rather than assumed.

**Progress is an approximation, and says so.** The API has no progress channel —
a conversion is one blocking call — so the bar is estimated client-side from the
source type, the file size, and the chosen engine (`ESTIMATES` in `app.js`:
~9 s for a URL render; ~2 s + 1.5 s/MB for PyMuPDF; ~8 s + 6 s/MB when a PyMuPDF
job is an image, i.e. OCR; ~30 s + 20 s/MB for docling; `auto` is estimated as
whichever of those `/ping` reports as `default_engine`, falling back to the fast
profile if the ping never answered). The fill follows
`1 - e^(-3t/estimate)`: 95% at the estimate, capped at 99%, so it keeps moving
instead of parking at "done"; past 1.3× the estimate the label says it is still
working and why. The response, not the timer, ends it.

**Viewer.** The output panel has *Preview* (rendered) and *Raw* (source)
modes. Rendering is `markdown.js`, a small renderer covering what this engine
emits. Converted content is untrusted: every fragment is HTML-escaped before any
markup is added (so raw HTML in the Markdown shows as text), link targets are
restricted to `http(s)`/`mailto`/relative, and `data:` images render as a
placeholder chip rather than being fetched (ADR-024). Raw view is the source of
truth; it is what Copy and Download return.

**Download.** Opens a dialog pre-filled with the document's own title — the first
`#` heading, or the first `##` if there is no `#`, or the source name as a last
resort — which the user may amend before confirming. The title becomes the
filename (path-illegal characters and whitespace → `-`, capped at 80 characters,
`.md` appended), previewed live in the dialog.
