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
    "version": "0.10.0",
    "engines": ["docling", "pymupdf"],
    "default_engine": "pymupdf",
    "mcp_endpoint": "/mcp",
    "keyword_methods": ["frequency", "yake"],
    "default_keyword_methods": ["frequency", "yake"]
  }
  ```
  `engines` lists the names a caller may pass as `engine` on a convert request
  (plus the always-accepted `auto`), so a client can offer the choice without
  hard-coding it. `default_engine` is the one `auto` resolves to *here* — i.e.
  this deployment's `WISEAU_PDF_ENGINE`, reported as the engine that will
  actually run (anything but `docling` runs the local parser). A client uses it
  to label its "auto" option and size a progress estimate (ADR-027).
  `mcp_endpoint` is the path of this deployment's MCP endpoint, or `null` if it
  serves none — `WISEAU_MCP_PATH` may have moved it, so a client discovers the
  connector URL rather than assuming `/mcp` (ADR-029).
  `keyword_methods` is the set of keyword methods this build can actually run
  (the optional ones are absent unless installed) and `default_keyword_methods`
  is what `POST /keywords` runs when a request names none — the same
  offer-what-works contract, applied to §17 (ADR-032).

### `POST /mcp` — the MCP endpoint
- **Purpose:** the agent-facing Model Context Protocol surface, served by this
  app rather than a separate process (ADR-029), so the deployed service URL plus
  this path *is* the connector URL any LLM is given. Speaks MCP's
  streamable-HTTP transport (JSON-RPC), **not** the REST contract above.
- **Path:** `WISEAU_MCP_PATH`, default `/mcp`; published by `/ping` as
  `mcp_endpoint`. Absent entirely when `WISEAU_MCP_MOUNT=0` or the optional MCP
  SDK is not installed.
- **Rate limit:** the default (`60/min`, `1000/day`) — it is an undecorated
  route like any other. The conversions it triggers are limited separately, on
  the convert routes it calls over loopback.
- **Not in `/openapi.json`.** The schema describes the REST contract; advertising
  a JSON-RPC endpoint inside it would mislead a function-calling client.
- **Tools and behaviour:** see [`mcp.md`](mcp.md). Deploying it: see
  [`hosting.md`](hosting.md).

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
  { "url": "https://example.com/article", "engine": "auto", "split_chapters": false }
  ```
  `url` is validated as an `HttpUrl`. `engine` is optional (see the box below);
  on this endpoint it applies only when the URL turns out to serve a **PDF** —
  an HTML page is extracted by Trafilatura regardless. `split_chapters` is
  optional and defaults to `false` (see §15).
- **200 response:** `MarkdownResponse` (see §3).
- **Errors:** `400` the URL resolves to a non-public address and this
  deployment refuses to fetch it (§13), or `engine` names an engine this build
  cannot run; `422` invalid URL (FastAPI validation); `429` rate limited;
  `502` extraction/render failure.

### `POST /convert/file`
- **Purpose:** parse an uploaded PDF, DOCX, or image into Markdown. Scanned /
  handwritten PDFs and image uploads are OCR'd automatically (see §10).
- **Rate limit:** `20/minute` per IP.
- **Request:** `multipart/form-data` with a `file` field and optional `engine`
  and `split_chapters` fields (see the box below and §15).
- **Constraints:** extension must be `.pdf`, `.docx`, or an image type
  (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`, `.gif`); body must
  be non-empty and ≤ `MAX_UPLOAD_BYTES` (default 25 MB). The limit is enforced
  while the part is *streamed*, so an oversized body is refused without being
  assembled in memory.
- **200 response:** `MarkdownResponse`.
- **Errors:** `400` empty upload or an unknown `engine`; `413` too large;
  `415` unsupported type; `429` rate limited; `502` parse failure.

### `POST /convert/batch`
- **Purpose:** convert several uploaded documents in one request, so "convert
  these thirty reports" is one call rather than thirty against a `20/minute`
  limit, and the client can offer them as one archive (§16, ADR-031).
- **Rate limit:** `5/minute` per IP — tighter than the single-document routes,
  because one request buys up to `MAX_BATCH_FILES` conversions.
- **Request:** `multipart/form-data` with the `files` part **repeated once per
  document**, plus an optional `engine`. `split_chapters` is accepted only as
  `false`.
- **Constraints:** at most `MAX_BATCH_FILES` documents (default 20); each
  document ≤ `MAX_UPLOAD_BYTES`; the batch as a whole ≤ `MAX_BATCH_BYTES`
  (default 50 MB). Documents are read and converted one at a time, so peak
  memory is one document, not the batch.
- **200 response:** `BatchResponse` (see §3) — returned whenever the *request*
  was valid, even if every document in it failed.
- **Errors:** `400` `split_chapters` was true (mutually exclusive — §16), an
  unknown `engine`, or more than `MAX_BATCH_FILES` documents; `413` the batch
  exceeds `MAX_BATCH_BYTES`; `422` no `files` part; `429` rate limited. A
  *document* that cannot be converted is **not** an error status: it is an entry
  with `status: "error"` inside a 200.

### `POST /keywords`
- **Purpose:** rank what an already-converted document is *about*, with the
  evidence behind each term (§17, ADR-032). A **second call**, not a flag on the
  convert routes: keywords are decided on after seeing the Markdown, so asking
  must not mean re-converting.
- **Rate limit:** `20/minute` per IP. Takes a job slot like any heavy work —
  `keybert` is CPU inference (invariant #4).
- **Request:** JSON — `markdown` (required), plus optional `source` (a label,
  echoed back; never fetched), `methods`, `top_k` (1-100, default 20),
  `language`, and `prepend_table`.
- **Constraints:** `markdown` ≤ `MAX_KEYWORD_CHARS` (default 2 000 000). Beyond
  `WISEAU_KEYWORD_MAX_CHARS` (400 000) only the leading portion is *analysed*,
  and the response's `note` says so.
- **200 response:** `KeywordResponse` (see §3). Returned even when nothing
  ranked — an empty list with a `note` is an answer a caller can act on.
- **Errors:** `400` empty `markdown`, or a `methods` name this build has never
  heard of; `413` past `MAX_KEYWORD_CHARS`; `422` `top_k` out of range or no
  `markdown` field; `429` rate limited; `502` the extraction itself failed.

> **The `methods` parameter (ADR-032).** Any of `frequency` (built in, always
> available), `yake`, `spacy`, `keybert` — or `["auto"]`/omitted for this
> deployment's `WISEAU_KEYWORD_METHODS`, or `["all"]` for everything it can run.
> `GET /ping` reports both (`keyword_methods`, `default_keyword_methods`), so a
> client offers only the choices that will work. Two failure modes are
> deliberately **different**: a name this build does not know is a **400** (a
> caller mistake, like an unknown `engine`), while a name it knows but cannot run
> — an optional package that is not installed, or a method that raised — is
> reported in `methods_skipped` and the rest still answer. Read `methods_used`,
> never assume.

---

> **The `engine` parameter (ADR-025).** Optional on all three convert endpoints:
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
| `chapters` | array \| null | The document split into chapters, or `null` when the split was not requested; `[]` when it was and nothing chapter-like was found (§15). |
| `chapter_detection` | string \| null | Which signal produced the chapters — `toc`, `headings`, `markers`, `none`, or `error` (the split failed; the conversion did not) — or `null` when the split was not requested. |

Each element of `chapters` is a `Chapter`:

| Field      | Type   | Meaning                                             |
| ---------- | ------ | --------------------------------------------------- |
| `title`    | string | The chapter's title as the document gives it.       |
| `level`    | int    | Heading depth of its opening (1-6); `0` for the material before the first chapter. |
| `filename` | string | Suggested filename, zero-padded so chapters sort in reading order (`03-the-reckoning.md`). |
| `markdown` | string | That chapter's Markdown, normalized like any other output. |
| `length`   | int    | `len(markdown)`.                                    |

Keep this shape additive: new fields may be appended, but existing fields must
not change type or meaning without a version bump (§6). `chapters` and
`chapter_detection` are `null` unless asked for, so a client that predates them
sees the response it always saw.

`BatchResponse` (returned by `POST /convert/batch`, ADR-031):

| Field       | Type   | Meaning                                            |
| ----------- | ------ | -------------------------------------------------- |
| `count`     | int    | How many documents were submitted.                  |
| `succeeded` | int    | How many converted.                                 |
| `failed`    | int    | How many did not. `succeeded + failed == count`.    |
| `results`   | array  | One `BatchItem` per document, **in the order they were sent**. |

Each element of `results` is a `BatchItem` — a `MarkdownResponse` plus three
fields:

| Field      | Type   | Meaning                                             |
| ---------- | ------ | --------------------------------------------------- |
| `status`   | string | `ok` or `error`, for this document alone.           |
| `filename` | string \| null | What to save it as, from the uploaded name and made unique within the batch (`annual-report.md`, `annual-report-2.md`). **Null on failure**, so a save loop over the items with a filename can never write an empty file in place of a document. |
| `error`    | string \| null | Why it failed — the message the equivalent single-document request would have returned. Null on success. |

`KeywordResponse` (returned by `POST /keywords`, ADR-032):

| Field             | Type   | Meaning                                       |
| ----------------- | ------ | --------------------------------------------- |
| `source`          | string | The label the caller sent, or `"document"`.   |
| `keyword_count`   | int    | `len(keywords)`.                              |
| `keywords`        | array  | The ranked keywords, best first.              |
| `methods_used`    | array  | The methods that **actually ran**, in canonical order. Read this rather than assuming the requested set ran. |
| `methods_skipped` | object | Requested method → why it did not run (not installed, or it raised). The request still succeeds. |
| `language`        | string | The language code the language-aware methods used. |
| `note`            | string \| null | Why the answer looks as it does when the list alone does not say: the document was too short to characterise, it was truncated at the analysis ceiling, or nothing ranked. Null otherwise. |
| `markdown`        | string \| null | The document with the keyword table prepended. **Null unless `prepend_table`** — a caller that did not ask gets the response it always got. |

Each element of `keywords` is a `Keyword`:

| Field         | Type   | Meaning                                           |
| ------------- | ------ | ------------------------------------------------- |
| `term`        | string | The keyword, in the form the document writes it (not its stemmed matching key). |
| `score`       | float  | Relative weight in `(0, 1]`, where the top-ranked keyword is exactly `1.0`. Fused across methods **by rank**, because their own scores are on incomparable scales. |
| `rank`        | int    | 1-based position in the fused ranking.            |
| `kind`        | string | `entity` when a method recognized it as a named entity, else `phrase`. |
| `occurrences` | int    | How often the term occurs in the document. Can be `0` for a term a method inferred rather than counted. |
| `agreement`   | int    | How many of the methods that ran found this term — the confidence signal. |
| `methods`     | object | Method name → `{rank, score}`: where that method placed the term and what it scored it **on its own scale** (a YAKE cost, a cosine similarity, a weighted count). |

A failed item carries `markdown: ""` and `length: 0`; `chapters` and
`chapter_detection` are always `null` in a batch (§16).

---

## 4. Module responsibilities

The backend is deliberately small and layered. Each module has one job.

| Module | Responsibility | Must not |
| ------ | -------------- | -------- |
| `main.py` | HTTP surface: routing, validation, CORS, rate limiting (decorator limits **and** `SlowAPIMiddleware` for the defaults), concurrency ceiling, streamed upload limits, error → HTTP mapping, per-request timing/logging. | Contain extraction logic; read a whole upload before checking its size. |
| `observability.py` | Structured (JSON) log formatting and the in-process metrics registry read by `GET /metrics`. Imported by `main.py` *and* the parsers. | Affect extraction output in any way; add a runtime dependency; record URLs, filenames, or content into `/metrics`. |
| `parsers/__init__.py` | Public entrypoints: `url_to_markdown`, `file_to_markdown`, `resolve_engine`, `default_engine`, `split_into_chapters`, `markdown_filename`, `unique_filenames`, `extract_keywords`, `resolve_methods`, `default_methods`, `available_methods`, `keyword_table`, `prepend_keyword_table`. | — |
| `parsers/browser.py` | Build a stealth headless Chrome driver; download a URL's raw bytes *through that driver's session* (`fetch_bytes`), so WAF clearance/cookies carry over. | Know about Markdown; raise on a failed download (return `None`). |
| `parsers/url_parser.py` | Refuse non-public addresses (§13), then render → Trafilatura extract → (markdownify fallback) → clean. Detect a direct-PDF response and route its bytes to the document pipeline instead. | Contain per-site CSS selectors; trust a `.pdf` URL without verifying the magic bytes; start the browser before the address is vetted. |
| `parsers/file_parser.py` | Select the conversion engine (the request's `engine`, else `WISEAU_PDF_ENGINE`, default `pymupdf`): PyMuPDF4LLM (legacy mode) + per-page OCR / Mammoth, or docling when it is selected — with automatic fallback to the local parsers. Dispatch by extension; then clean. Validate a caller's engine choice (`resolve_engine`); report the deployment default (`default_engine`). | Hard-depend on docling; return unnormalized text; inline a DOCX image as a base64 data URI (ADR-024); use PyMuPDF4LLM's unstable layout/OCR engine in the fallback. |
| `parsers/docling_client.py` *(Phase 6)* | Thin HTTP client to docling-serve (`WISEAU_DOCLING_BASE`): document bytes → Markdown, images requested as placeholders (ADR-024). Bounded timeout; typed errors so the caller can tell "docling down" from "bad document". | Contain conversion logic itself; retry forever; leak the token. |
| `parsers/ocr.py` | Pluggable OCR engines (default MuPDF-Tesseract, opt-in EasyOCR): page image → text. Used by the *fallback* PDF path. | Introduce nondeterminism. |
| `parsers/cleaner.py` | Deterministic Unicode/whitespace/typography normalization; elide base64 data-URI payloads (ADR-024). | Introduce nondeterminism; remove content (it edits payloads, not text). |
| `parsers/chapters.py` *(Phase 10)* | Split converted Markdown into chapters: read the document's contents page, else its heading structure, else plain-text chapter markers; name and number each chapter's file (§15). | Extract or convert anything; drop or duplicate content; return a split it is not confident in (say `none` instead); introduce nondeterminism. |
| `parsers/keywords.py` *(Phase 12)* | Rank the keywords of already-converted Markdown across four methods and fuse the rankings; render the result as a Markdown table and prepend it to a document (§17). | Convert or extract anything; hard-depend on an optional method; let one method's failure fail the request; return a ranking for a document too short to characterise. |
| `parsers/naming.py` *(Phase 11)* | The one filename rule, shared by chapter splitting and bulk conversion: slug a title or an uploaded name into a `.md` file, and make a batch's names unique in order (§16). | Emit a name containing a path component; depend on anything but its input (it must stay deterministic). |

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
| `PORT` | `7860` | Listen port. The default matches Hugging Face Spaces; Cloud Run and Render inject their own and the image binds whatever is set (ADR-029). Also the port `mcp_server`'s loopback calls target. |
| `WISEAU_MCP_MOUNT` | `1` | Serve the MCP endpoint from this app (ADR-029). `0` leaves only the REST API; a checkout without the optional MCP SDK degrades to the same, with a warning. |
| `WISEAU_MCP_PATH` | `/mcp` | Path of that endpoint. An unguessable value makes the connector URL a shared secret — obscurity, not authentication. |
| `WISEAU_MCP_ALLOWED_HOSTS` | unset (check off) | Comma-separated hostnames whose `Host` header the MCP endpoint accepts. Unset or `*` disables the check, which is what a public deployment needs; naming hosts enforces them (ADR-029). |
| `WISEAU_API_BASE` | `http://127.0.0.1:$PORT` | Where the MCP tools reach the API. The default is this process, over loopback. |
| `MAX_CONCURRENT_JOBS` | `4` | Global concurrency ceiling for heavy jobs. |
| `MAX_UPLOAD_BYTES` | `26214400` | Upload size limit (25 MB), per document. |
| `MAX_BATCH_FILES` | `20` | Most documents one `POST /convert/batch` may carry (ADR-031). This, not memory, is what bounds how long one caller can occupy the queue — lower it on a deployment behind a proxy with a short idle timeout, especially if batches use `engine=docling`. |
| `MAX_BATCH_BYTES` | `52428800` | Total upload bytes one batch may carry (50 MB). Exceeding it mid-batch is a request-level 413. |
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
| `MAX_KEYWORD_CHARS` | `2000000` | Longest document `POST /keywords` accepts, in characters (ADR-032). The body is text in JSON, not an upload, so the streaming upload guard cannot bound it. Exceeding it is a 413. |
| `WISEAU_KEYWORD_METHODS` | unset (`frequency,yake`) | Default keyword methods for this deployment: a comma-separated list, `auto` (the cheap pair), or `all` (everything installed). A request's `methods` overrides it. Names that are not installed are dropped; if nothing is left, `frequency` runs. |
| `WISEAU_KEYWORD_LANG` | `en` | Language code the language-aware methods assume when a request names none. |
| `WISEAU_KEYWORD_MAX_CHARS` | `400000` | How much of a document any keyword method reads. A cap, not a refusal: a longer document still extracts, and the response's `note` says it was truncated. |
| `WISEAU_SPACY_MODEL` | `en_core_web_sm` | spaCy pipeline for the `spacy` method. Must be installed (`requirements-keywords.txt`). |
| `WISEAU_KEYBERT_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer for the `keybert` method. Weights download on first use. |
| `WISEAU_ALLOW_PRIVATE_URLS` | unset (off) | Allow `/convert/url` to fetch loopback/private/link-local addresses. Off by default (ADR-021); set to `1` for a self-hosted deployment that converts its own intranet. |
| `WISEAU_LOG_FORMAT` | `json` | Log rendering: `json` (one object per line, for aggregators) or `text` (human-readable, for local work). |
| `WISEAU_LOG_LEVEL` | `INFO` | Root log level. |

Rate limits are code-level constants in `main.py` (`60/min` + `1000/day` default;
`20/min` on the single-document convert routes and on `/keywords`, `5/min` on
`/convert/batch`, which buys up to `MAX_BATCH_FILES` conversions per token).
Promote them to env
vars only if a real tuning need arises — record the change in `decisions.md`.

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
GitHub Pages ──HTTPS──► service #1: FastAPI + Chromium ──HTTP──► service #2: docling-serve
 static frontend         backend + MCP endpoint, guards            PyTorch converter
                         16 GB / 2 vCPU                            16 GB / 2 vCPU  (Phase 6)
      any LLM ──MCP/HTTPS──►  (same URL, /mcp)
```

Service #1 is one container serving three audiences from one port: the REST API
the frontend calls, the OpenAPI schema a function-calling agent reads, and the
MCP connector URL an LLM is given (ADR-029). It runs on any host that can build a
Dockerfile and give it a public HTTPS URL — Hugging Face Spaces (the repo carries
the Space card), Google Cloud Run, or Render. [`hosting.md`](hosting.md) is the
click-by-click guide; the notes below are what a host has to satisfy.

- Backend image version-locks Chromium + Python via the `Dockerfile`; runs as
  non-root UID 1000 (Hugging Face requirement) and binds `$PORT`, defaulting to
  7860 — so a host that injects its own port (Cloud Run's 8080, Render's 10000)
  needs no change. It also installs `requirements-mcp.txt`, because the deployed
  container serves the MCP endpoint itself. Sizing: ~2 GiB of memory, since
  headless Chromium plus a PDF parse will exceed 512 MB. A Docker Space takes
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
- **How a client reaches those tools** is a transport choice, not a contract
  change. The deployed backend serves them over HTTP at `WISEAU_MCP_PATH`
  (default `/mcp`), so a hosted LLM gets a connector URL and there is no second
  service to run (ADR-029); the same module also runs standalone over `stdio` for
  an agent on the operator's own machine, or over its own HTTP port (ADR-028).
  In the hosted case `WISEAU_API_BASE` points at loopback — the guards are
  preserved precisely because the call is still HTTP. One consequence to know:
  all hosted MCP traffic then shares the loopback address's rate-limit bucket.
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
| `chapters` | `requested` / `split` / `sections` and `by_method` (`toc`, `headings`, `markers`, `none`). | Is chapter detection actually finding chapters, and by which signal? |
| `batches` | `requested` / `files` / `failed` / `largest` (ADR-031). | What bulk conversion actually costs — thirty documents and one document are both a single request in `by_route`, so `files` is what sizes `MAX_BATCH_FILES`, and a climbing `failed` means callers are sending something unreadable. |
| `keywords` | `requested` / `keywords` / `empty`, plus `by_method` and `skipped` (ADR-032). | Which methods are *actually* running — a deployment that installed the optional extras but never set `WISEAU_KEYWORD_METHODS` looks busy while only ever running the cheap pair. `skipped` is the only place a missing optional package shows, since it degrades the answer silently by design. |
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
`app.js` + `markdown.js` + `zip.js` + `config.js` + `favicon.svg`) served
straight from GitHub Pages. No framework, no build step, no third-party script
(ADR-004).

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

**Chapters.** A *Split into chapters* checkbox sends `split_chapters` (§2). When
the response carries chapters, a panel above the output lists them — title,
suggested filename, size — and says which signal found them ("found on the
document's contents page" reads differently from "found from the document's
headings", and the reader should be able to weigh that). Each row can **View**
the chapter in the output panel (Copy and Download then act on that chapter) or
**Save** it as its own `.md`; *Whole document* goes back. **Download all (.zip)**
builds the archive in the browser with `zip.js` — stored entries, no compression,
a fixed 1980-01-01 timestamp so the same conversion yields byte-identical
archives. One archive rather than *n* saves, because browsers block a burst of
downloads. A requested split that found nothing says "no chapters detected" in
the caption and leaves the document whole.

**Keywords.** A *Keywords* button sits with Copy and Download, and is enabled
only once there is Markdown — the feature is deliberately post-conversion
(§17), so there is nothing about it in the input column. Clicking it calls
`POST /keywords` for whatever the output panel is showing and opens a panel with
the ranked table: term, weight (as a number and a bar), occurrences, and the
methods that found it, with each row's tooltip carrying every method's own rank
and score. Method chips are built from `/ping`'s `keyword_methods`, ticked from
`default_keyword_methods`, and re-extract on change; unticking the last one is
refused rather than sending an empty set. **Download .json** saves the API's own
answer minus the document as `<title>.keywords.json` — no timestamp, so two
extractions of a document produce identical files, the same reason `zip.js`
pins its dates. **Add to Markdown** swaps in the annotated document the same
response already carried (`prepend_table` is always requested, so the button
costs no round-trip), and becomes **Remove from Markdown**; Copy, Download, a
chapter's Save and the archive all follow it. Because keywords describe the
*shown* document, switching chapter, batch document, or conversion clears the
panel rather than leaving a stale ranking attached to something else.

---

## 15. Chapter splitting (ADR-030)

A long PDF converts to one long Markdown string, which is the wrong shape for the
reader who wanted one file per chapter. `parsers/chapters.py` slices the
*converted* Markdown — it runs after extraction and after `clean_markdown`, so it
is engine-independent: docling, PyMuPDF, Mammoth, and OCR output all arrive the
same way.

**Opt-in.** `split_chapters` defaults to `false` on `/convert/url` and
`/convert/file` and on their two MCP tools. It roughly doubles the response, and
most documents have no chapters, so it is asked for rather than charged to
everyone. It is **not available on `/convert/batch`** at all — bulk conversion
and chapter splitting are mutually exclusive for now (§16, ADR-031).

**How chapters are found**, in order of how much the document itself tells us:

1. **`toc` — the contents page.** A chaptered document nearly always prints its
   chapters in a table of contents, and that page survives extraction as a run of
   "title ..... 12" lines (or a table, or a list of links). Those entries are the
   *author's* list of chapters, so they are tried first: parse the entries, take
   the shallowest depth (sub-sections indent, or number as `1.1`), then find each
   entry again in the body, strictly **forward** — each match must come after the
   previous one. Matching is exact on a normalized title first (case, markup,
   punctuation and leading numbering removed), then fuzzy (difflib ≥ 0.86) over
   heading-like lines. The block is believed only if **at least half** its
   top-level entries are found that way, and at least two. That gate is what
   rejects a back-of-book index: its entries do not reappear in order below it.
2. **`headings` — the heading structure.** The shallowest level occurring more
   than once, unless a deeper level is visibly the chapter level (≥60% of its
   headings read as "Chapter 4" / "Part II" / "Appendix A" / "3. Findings").
3. **`markers` — plain-text chapter openings.** Scans and single-font PDFs arrive
   with no heading markup, so a bare line reading "CHAPTER FOUR" is the last
   signal worth using.
4. **`none`.** Nothing chapter-like, or the result looks like fragments rather
   than chapters (median chapter under 200 characters, or more than 500 of them).
   The document is returned whole; an article is not a book.

**Guarantees.** The chapters **partition** the document: concatenating them in
order reproduces the input, modulo the whitespace normalization each chapter goes
through. Nothing is dropped and nothing is duplicated — material before the first
chapter (title page, the contents page itself) becomes a leading section at
`level: 0`, unless it is shorter than 100 characters, in which case it opens the
first chapter rather than becoming an almost-empty file. Filenames are
zero-padded to the width of the largest number, so alphabetical order is reading
order. And the split is **deterministic**: no model, only text, so the same
Markdown always yields the same chapters, titles, and filenames (invariant #1).

**Failure is contained.** The document already converted — sometimes after a
minute of docling — and the split is an extra on top of it, so an exception in
the heuristics returns `chapters: []` with `chapter_detection: "error"`, logs the
traceback, and counts the method as `error`. It never turns a good conversion
into a 502, and it is never silent.

**Cost.** Line-scanning and normalization, single-pass. Measured: ~160 ms for a
0.7 MB / 400-chapter book, ~1 s worst case (a contents page whose entries match
nothing, over 40 000 short lines). It runs via `asyncio.to_thread` *inside* the
job slot, so it queues behind the same concurrency ceiling as the conversion
(invariant #4).

---

## 16. Bulk conversion (ADR-031)

Converting a folder of documents through `/convert/file` costs one rate-limit
token each, so a thirty-file job is throttled halfway through and the user is
left with a partial result. `POST /convert/batch` is the same conversion, N times,
behind one request — and the client turns the answer into one archive.

**It is not a new kind of conversion.** Each document takes exactly the path
`/convert/file` would take it through — same parsers, same engine selection, same
`clean_markdown` (invariant #3) — one at a time, **each taking its own slot** in
the concurrency ceiling. Per document rather than per batch is deliberate:
holding the ceiling for a whole twenty-file run would starve every other caller
for minutes, so a large batch queues fairly alongside them (invariant #4).

**Partial success is the normal case.** The response is 200 whenever the
*request* was valid, with per-document outcomes inside it. A document that cannot
be read carries the message the single-document endpoint would have returned
(unsupported type, empty upload, a parse failure) and the rest still convert —
one bad file in a folder must not cost the other twenty-nine. A failed item has
**no `filename`**, which makes "write every item that has a filename" the whole
of a correct save loop.

**Filenames** come from the backend, via `parsers/naming.py`, which chapter
splitting uses too — one rule, one place. An uploaded `Annual Report 2025.pdf`
becomes `annual-report-2025.md`; a second document of the same name becomes
`annual-report-2025-2.md`, deduplicated in upload order so the naming stays
deterministic. Directory components are stripped and every non-word character
becomes a hyphen, so a suggested filename can never be a path: the *uploader*
chooses that string and the client writes a file under it.

**The ZIP is built by the client**, not the API (`frontend/zip.js`, from ADR-030).
The API's job is the contract: a JSON response is inspectable, diffable, and
directly usable by an agent, where an archive is bytes that must be unpacked
before anything can read them. The browser already had a deterministic ZIP writer
for chapters, and a batch is the same list-of-files problem.

**Bounds.** One request buys up to `MAX_BATCH_FILES` conversions, so the batch
needs guards of its own or it *is* the way around the fair-use limits:
`5/minute` per IP, at most `MAX_BATCH_FILES` (20) documents, at most
`MAX_BATCH_BYTES` (50 MB) in total, each document still under
`MAX_UPLOAD_BYTES`. Documents are read and converted one at a time, so peak
memory is one document. Exhausting the batch budget is a request-level **413**
(nothing after that point could have converted either); one outsized document
among ordinary ones is that document's own error.

### Mutually exclusive with chapter splitting

Bulk conversion and chapter splitting (§15) **cannot be combined** — for now, by
decision rather than by accident, because there is no good answer yet to what an
archive of twelve documents' chapters should look like. It is enforced in three
places, so lifting it later has to be deliberate:

- **API** — `split_chapters=true` on `/convert/batch` is a **400**, never a
  silently dropped flag. `chapters` and `chapter_detection` are always `null` in
  a batch result.
- **MCP** — `convert_batch` has no `split_chapters` parameter at all. An agent
  chooses a tool by its parameters, so offering one the backend refuses would be
  a promise it cannot keep.
- **UI** — selecting a second file disables the *Split into chapters* checkbox,
  **unticks** it, and says why. A checkbox left ticked while being ignored is how
  a user comes to believe they asked for chapters and got none.

The UI expresses the same thing structurally: the chapter list and the batch's
document list are **one panel**, because only one of them can ever be showing.

---

## 17. Keyword extraction (ADR-032)

A conversion says what a document *contains*; `parsers/keywords.py` says what it
is *about*. It runs on already-extracted Markdown, after `clean_markdown`, so it
is engine-independent — docling, PyMuPDF, Mammoth, OCR and Trafilatura output
all arrive the same way, exactly like chapter splitting (§15).

**A second call, not a flag.** `POST /keywords` takes the Markdown itself. That
is the whole design: keywords are something a reader wants having *seen* the
document, so a `keywords: true` on the convert routes would mean re-converting —
up to a minute of docling on a scanned report — every time someone changed their
mind about the method set. Taking the document instead means a second opinion
costs the extraction alone, and works equally on Markdown from anywhere.

**Four methods, different in kind.** The point is not to pick a winner; it is to
see where independent signals agree.

| Method | What it sees | Ships in | Cost |
| ------ | ------------ | -------- | ---- |
| `frequency` | Candidate phrases between stopwords, weighted by **where** they appear — the title and headings outweigh a paragraph. The only method that reads the document's structure. | built in, no dependency | milliseconds |
| `yake` | YAKE!'s statistical features: casing, position, dispersion. Score is a **cost** — lower is better. | `requirements.txt` | milliseconds |
| `spacy` | Noun chunks and **named entities** — people, organisations, statutes, places: the terms a frequency count under-ranks because they are rare. | `requirements-keywords.txt` | ~1s + model load |
| `keybert` | Semantic relevance: cosine similarity between a candidate and the document's own embedding. The only method that can rank a term the document barely repeats but is entirely about. | `requirements-keywords.txt` | seconds to a minute on free CPU, plus a first-use model download |

`frequency` is the floor: it needs nothing, so the endpoint always answers even
on the leanest deployment (ADR-014's rule — never hard-depend on an optional
component). `WISEAU_KEYWORD_METHODS` sets the deployment default; a request's
`methods` overrides it (ADR-025's shape).

**Fusion is by rank, never by score.** The four scores are mutually
incomparable — a YAKE cost of 0.04, a cosine of 0.61 and a count of 17 cannot be
averaged, and normalizing them against each other invents a relationship that is
not there. So each method's output is used only for the **order** it produces,
and the orders combine by Reciprocal Rank Fusion: a term scores
`Σ 1/(60 + rank)` over the methods that ranked it. Scale-free, needs no tuning,
and a method that is unavailable contributes nothing while the rest still rank.
The reported `score` divides that sum by the winner's, so the top term is `1.00`
and the rest read as relative weights. Ties break on the term itself.

**Terms are matched canonically, displayed as written.** `"The Climate Changes"`
and `"climate change"` key to the same term — lowercased, boundary words
trimmed, plurals folded by a conservative S-stemmer — so four phrasings of one
idea fuse into one keyword instead of competing for four rows. What is
*displayed* is the surface form the document actually uses, so a document about
"Analytics" never shows "analytic". A single word whose every occurrence sits
inside a better-ranked phrase is dropped ("climate" under "climate change");
one that also stands alone keeps its row. A **document's own section names** — "Executive summary",
"Findings", "Recommendations" — get no heading prominence: they appear in
headings in every report regardless of subject, so crediting them there makes
them the top keywords of everything. They can still rank on genuine repetition
in the body, which is what a document actually *about* its recommendations
supplies.

**Two text-preparation rules earn their place**, both found by driving real PDFs:

- A **single newline is not a phrase boundary.** Extracted PDF text is
  hard-wrapped, so "the Green\nClimate Fund" is one phrase crossing a line.
  Treating every line end as a break reported zero occurrences for it and kept
  `frequency` from proposing the one entity the document was about. Only
  punctuation and **blank** lines separate phrases.
- **Every block-final line is terminated with a full stop.** A blank line is not
  a sentence boundary to the tokenizers these methods use, and most scanned or
  single-font PDFs extract with no heading markup — so a bare "Executive
  summary" ran into the paragraph below it and YAKE returned "Pacific Executive
  summary" as a top keyword. Headings, list items and one-line paragraphs are
  closed; a wrapped paragraph's interior line breaks are left alone.

**`keybert` is a re-ranker, not a fifth candidate generator.** It receives the
candidates the other methods pooled, so the fusion measures agreement over a
shared vocabulary rather than comparing disjoint lists, and scoring a bounded
candidate set is a bounded amount of inference. Two things about it are not
obvious and must not be "simplified" back:

- It is given an **explicit `CountVectorizer`**. KeyBERT's default one lowercases
  the document but not the supplied vocabulary, defaults to unigrams, and strips
  English stopwords before forming n-grams — so every capitalized term, every
  phrase, and every "X of Y" was dropped *silently*, producing a shorter list
  rather than an error.
- Its **document embedding is the mean of embeddings sampled across the whole
  document**. A sentence-transformer truncates at a few hundred tokens, so the
  obvious call ranks every candidate against the report's cover page.

**It says how sure it is, and refuses rather than guesses** (§15's rule). Every
keyword carries `agreement` and the rank and native score each method gave it, so
a term all four found is visibly different from one a single method liked. A
document under 200 characters of text returns **no** keywords and a `note`
saying why, rather than promoting whichever noun occurred twice. A method that
is not installed, or that raises, is reported in `methods_skipped` while the
others still answer — so a caller reads `methods_used`, never assumes.

**Deterministic** (invariant #1). Every method is deterministic for fixed inputs,
`keybert` included: fixed weights, CPU inference, pinned seed — the bargain
`ocr.py` strikes for EasyOCR. Nothing here is ordered by a dict.

**Annotating the document.** `prepend_table` returns the document with the
keywords as a Markdown table, inserted **after its own opening heading** where it
has one and at the very top otherwise — a file whose first line is `## Keywords`
has lost its title to a summary of itself, and every downstream reader takes the
first heading as what the document *is*. The block is fenced in HTML comments
(`<!-- wiseau:keywords -->`), with blank lines around the table so it is
well-formed Markdown in its own right. Those markers make the operation
**idempotent**: prepending twice replaces the block, and extraction run on an
annotated document analyses the document rather than its own output. The
frontend's renderer skips HTML comments, so the markers are never displayed.

**Cost and bounds.** `frequency` + `yake` answer a report in tens of
milliseconds; all four, warm, in a few seconds; `keybert` cold pays a model
download. It runs via `asyncio.to_thread` *inside* the job slot, so it queues
behind the same concurrency ceiling as a conversion (invariant #4), under
`20/minute`, with `MAX_KEYWORD_CHARS` as a 413 and `WISEAU_KEYWORD_MAX_CHARS` as
the analysis ceiling.

**No batch form.** A folder of twenty documents is twenty calls against a
`20/minute` limit — the same problem ADR-031 solved for conversion, deliberately
left open here rather than guessed at. In the UI, keywords describe **whatever
the output panel is currently showing**: the whole document, one chapter, or one
document of a batch. Switching what is shown clears the panel.
