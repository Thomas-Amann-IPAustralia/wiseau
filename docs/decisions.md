# Architecture Decision Log (ADRs)

A running record of **non-trivial decisions and their rationale**, so a later
Claude Code instance doesn't relitigate a settled choice — or, if it should be
revisited, can see exactly what was weighed.

Add a new entry at the top when you make a decision that future work would
otherwise have to reverse-engineer. Keep entries short. When a decision is
overturned, don't delete it — add a new entry that supersedes it and mark the old
one `Superseded`.

**Entry template:**

```
## ADR-NNN — <short title>
**Date:** YYYY-MM-DD · **Status:** Accepted | Superseded by ADR-XXX | Proposed
**Context:** what forced a choice.
**Decision:** what we chose.
**Consequences:** what this makes easy/hard; what to watch for.
```

---

## ADR-027 — The default engine is the fast local parser; docling is opt-in
**Date:** 2026-07-30 · **Status:** Accepted · **Amends:** ADR-013, ADR-014
**Context:** ADR-013/014 made docling the *default* document engine, on the
argument that fidelity outranks speed. Living with the choice inverted the
balance. A default is the setting that applies to every document nobody thought
about, and for the ordinary document — a born-digital PDF or a DOCX with a text
layer — PyMuPDF/Mammoth returns good Markdown in about a second, where docling
on a free CPU Space costs tens of seconds to minutes and can cold-start into
minutes more. Making that the default charges every conversion the worst-case
price for a fidelity gain only *some* documents need, and ADR-014's fallback
does not soften it: waiting out `WISEAU_DOCLING_TIMEOUT` (120s) before falling
back is the *slowest* possible way to produce the fast parser's output. Since
ADR-025 the caller can name the engine per request anyway, so fidelity no longer
has to be bought with the default.
**Decision:** `WISEAU_PDF_ENGINE` now defaults to **`pymupdf`**. docling stays a
first-class engine, reachable two ways: per deployment
(`WISEAU_PDF_ENGINE=docling`) and per request (`engine="docling"`, ADR-025),
with ADR-014's automatic fallback unchanged in both cases. Because "auto" now
resolves differently depending on the deployment, `GET /ping` reports
`default_engine` alongside the engine list, so the UI can label its *Auto*
option and size its progress estimate from what the server will actually do
rather than from a hard-coded assumption. API `0.6.0 → 0.7.0` (additive field;
the version bump also marks the behaviour change). The UI lists *Fastest* ahead
of *Highest fidelity* and says which one is the default.
**Consequences:** The common case is fast again, and a deployment with no
docling Space configured behaves identically to one with a docling Space it
doesn't reach for — no wasted attempt, no `not_configured` skip; the metric now
reads `engine_not_selected`. The cost is that a table-heavy or scanned document
converts at PyMuPDF fidelity unless someone *asks* for docling — the UI's engine
picker and the `engine` argument on both MCP tools are how they ask, and the
picker's note explains when to. This narrows ADR-013 rather than reversing it:
fidelity is still what docling is *for*, and its output is still allowed to vary
run-to-run by design. What changes is that the stochastic path is now opted into
rather than defaulted into, so a stock deployment is deterministic end-to-end. If
free-tier docling ever gets fast enough — or a deployment runs it on paid
hardware — flipping the default back is one env var, and that is exactly what
`WISEAU_PDF_ENGINE=docling` is for.

## ADR-026 — The UI approximates progress and renders Markdown itself, with no new dependency
**Date:** 2026-07-29 · **Status:** Accepted
**Context:** The output panel showed raw Markdown in a `<pre>` and gave no
feedback while a conversion ran — acceptable when every conversion took a
second, misleading now that choosing docling can mean minutes of an apparently
frozen button. Two wants followed: a progress indicator, and a rendered
("pretty") view alongside the raw syntax. Neither is free: the API is one
blocking call with **no progress channel** (a conversion is a single
`asyncio.to_thread` job behind a semaphore — adding real progress would mean job
IDs, polling, and server-side state, i.e. a different API), and rendering
Markdown usually means pulling in a library, which the frontend has deliberately
avoided (no framework, no build step — ADR-004).
**Decision:** (1) **Approximate the progress bar client-side** from what the
client already knows — source type, file size, and the chosen engine — with an
asymptotic curve (95% at the estimate, capped at 99%) that keeps climbing rather
than parking at the end, plus an elapsed timer and a "still working" note past
1.3× the estimate. The estimate is labelled as one ("about 30s"), never as
measured progress. (2) **Ship a small renderer** (`frontend/markdown.js`, ~200
lines, no dependency) covering the subset the engine emits: headings,
paragraphs, fenced code, lists, blockquotes, pipe tables, rules, inline marks.
Extracted content is untrusted, so every fragment is HTML-escaped *before* any
markup is added (raw HTML in the Markdown displays as text), link targets are
restricted to `http(s)`/`mailto`/relative, and `data:` images render as a
placeholder chip instead of being loaded (ADR-024). (3) Downloads go through a
dialog that pre-fills the title from the document's first `#` — or first `##`
when there is no `#` — and lets the user amend it before confirming; the title
names the file.
**Consequences:** The UI stays a static, dependency-free bundle that GitHub
Pages can serve as-is, and it now reads as responsive during a long docling
conversion. The bar is an *estimate*: a cold docling Space can overshoot it
badly, which is why it never claims to be finished. The renderer covers what
this engine produces, not all of CommonMark — reference links, setext headings,
and nested block quirks are out of scope; the raw view is always one click away
and is the source of truth. If real progress is ever wanted, it needs the job-ID
API above, not a better estimate.

## ADR-025 — The conversion engine is a per-request choice, not just a deployment setting
**Date:** 2026-07-29 · **Status:** Accepted
**Context:** Which engine converts a document was a deployment-wide env var
(`WISEAU_PDF_ENGINE`). But the trade-off it encodes is *per document*, not per
deployment: docling reads a scanned, multi-column government PDF far more
faithfully, and takes tens of seconds to minutes on free CPU to do it, where
PyMuPDF returns a decent answer in about a second. Only the person (or agent)
holding the document knows which they want this time, and the UI could not ask.
**Decision:** Accept an optional `engine` on both convert endpoints —
`docling` | `pymupdf` | `auto` (default; defer to `WISEAU_PDF_ENGINE`) — carried
as a JSON field on `/convert/url` and a form field on `/convert/file`, and
exposed as an argument on both MCP tools. It is validated in `main.py`
(`resolve_engine`), so an unknown name is a **400**, never a silent substitution
or a 502. `auto` resolves to `None` rather than to a guessed engine name, so a
request never pins an engine it did not ask for. API `0.5.0 → 0.6.0` (additive);
`GET /ping` now also advertises the accepted engine names so a client can offer
the choice without hard-coding it. ADR-014's automatic fallback still applies to
an *explicitly requested* docling: choosing fidelity must not cost resilience.
On `/convert/url` the choice reaches the document pipeline only when the URL
turns out to serve a PDF — an HTML page is Trafilatura's either way.
**Consequences:** The UI can offer "highest fidelity vs fastest" with an honest
warning about docling's cost, and agents get the same lever. `WISEAU_PDF_ENGINE`
keeps its meaning as the *default*. The response does not (yet) report which
engine actually ran, so a caller who asks for docling and silently gets the
fallback cannot tell from the response alone — `GET /metrics` still holds that
answer, and adding an `engine` field to `MarkdownResponse` remains an option
(§3 is additive by design).

## ADR-024 — Inlined base64 images are stripped: payload out, structure kept
**Date:** 2026-07-29 · **Status:** Accepted
**Context:** A conversion came back with an image inlined as a base64 PNG data
URI: one unreadable string tens of thousands of characters long, dwarfing the
document's actual text. Two paths produce it, and both are *defaults* of
upstream tools rather than anything this pipeline asks for: Mammoth's default
image handler (`mammoth.images.data_uri`) inlines every DOCX picture, and
docling-serve's default `image_export_mode=embedded` returns every figure the
same way. The product is *readable Markdown for humans and LLMs*; a base64 blob
serves neither, and it inflates every consumer's token count and the response
size for content nobody can read.
**Decision:** Defend at the source and at the exit. At the source: the DOCX path
converts images with a handler that emits no `src` at all, and the docling
client sends `image_export_mode=placeholder` (docling then emits a short
`<!-- image -->` marker). At the exit: `clean_markdown` — the shared normalizer
every parser already flows through (invariant #3) — elides the payload of any
base64 data URI that still reaches it, wherever it appears (Markdown image,
HTML attribute, or bare text), leaving `data:image/png;base64,...`. It is
payload-only: no image, link, or paragraph is removed, and there is no size
heuristic, so the rule stays a normalization and stays deterministic.
**Consequences:** An illustrated document converts to Markdown about its text's
size again, and *where* the figures were is still recorded. The image data is
gone — this engine extracts text, and a caller who needs the images must go to
the source document. Rejected: stripping only "large" payloads (a threshold to
tune, and a small blob is no more readable), and deleting the image syntax
entirely (loses the fact that a figure was there). The frontend viewer renders
an elided image as a placeholder chip rather than a broken-image icon.

## ADR-023 — deployment is prepared in-repo so the remaining work is account-only
**Date:** 2026-07-26 · **Status:** Accepted — implemented; not yet exercised against a real Space or Pages site.
**Context:** Both open tracks (Phase 5 deployment, Phase 6's docling Space) were
described as "needs external accounts, not code". Reading the repo against what a
deployment actually requires found two things that *were* code, and both would
have failed on the first attempt. (1) A Hugging Face Docker Space takes its
configuration from YAML frontmatter in the Space repo's `README.md` —
`docling/README.md` had a Space card, `backend/README.md` did not, so Space #1
had no `sdk: docker` and no `app_port`. (2) GitHub Pages' branch publishing
serves a repository root or `/docs` only, and `/docs` holds the project
documentation; the UI lives in `frontend/`, so "activate GitHub Pages" as the
roadmap phrased it was not a setting anyone could switch on. Separately,
`config.js` is documented as *the* per-deployment knob, which meant pointing the
published site at a Space required committing the Space URL — turning the last
step of deployment back into a code change.
**Decision:** Add the Space card frontmatter to `backend/README.md` (mirroring
`docling/`), and publish `frontend/` with an explicit Pages workflow
(`.github/workflows/deploy-frontend.yml`) using `upload-pages-artifact` /
`deploy-pages`. Keep `config.js` as the committed knob and its localhost default,
but let the workflow overwrite the assignment **in the uploaded copy only** when
the `MARKDOWN_API_BASE` repository variable is set. An absolute-URL check fails
the job rather than publishing a site that silently calls localhost.
**Consequences:** Everything left to reach "operational" is account work —
create two Spaces, push `backend/` and `docling/` to them, set the secrets, set
one repository variable, enable Pages. Nothing left to deploy needs a commit. The
cost is a second place where the API base can come from: the published site can
disagree with `config.js` in the tree. That is deliberate (a public repo should
not have to carry the deployment's URL) and the workflow logs the value it wrote,
but a future instance debugging "the site points at the wrong backend" should
check the repository variable before the file. Local development and any
non-Pages host are unaffected — with the variable unset, the committed default
ships unchanged. Neither half is proven: no Space and no Pages site exist yet, so
the frontmatter and the workflow are written-but-unrun until someone with the
accounts runs them.

---

## ADR-022 — bound the short-page repair by the size of the *repeat*, not the document
**Date:** 2026-07-26 · **Status:** Accepted — amends ADR-020; implemented and unit-tested.
**Context:** ADR-020's guard rails were meant to make the repair unable to touch a
document the upstream bug cannot affect, and the code, its comments and
tech-spec §4 all claimed exactly that ("a document long enough to be unaffected by
the upstream bug is not even scanned"). It was not true. The only size guard was a
**block count** (≤ 60 blocks), but Trafilatura's bug is triggered by an
**extraction under 250 characters** — an entirely different measure. A document of
five blocks and 851 characters is 3.4× over Trafilatura's threshold, so any repeat
in it is genuine content, yet it was scanned and a legitimately repeated paragraph
was silently deleted. `recover_wild_text` can also append far more than it
duplicates, so the *document's* length says nothing about whether the bug fired;
the surviving signal is the size of the duplicated run itself.
**Decision:** Add `_MAX_REPEATED_RUN_CHARS = 250` — Trafilatura's own
`MIN_EXTRACTED_SIZE` — as an upper bound on the repeat the repair will drop. The
run it duplicates *is* the body it had already extracted, and it only reaches for
the rescue when that body came in under this threshold, so a larger adjacent
repeat provably is not the artifact. The block cap stays, demoted to what it
actually is: a bound on the scan's cost, not a statement about safety.
**Consequences:** The lossy case ADR-020 accepted shrinks to repeats between 40 and
250 characters — a page that prints the same *substantial* passage twice now
survives intact, which is the shape that made the old guard dangerous in the
target corpus (repeated disclaimers on government notices). A short duplicated run
is still repaired inside a long page, which is correct: the rescue precisely
produces a small duplicate followed by a large recovered tail. Delete the whole
repair when upstream stops double-counting, per ADR-020.

## ADR-021 — refuse non-public addresses on `/convert/url`
**Date:** 2026-07-26 · **Status:** Accepted — implemented, unit-tested, and verified live against real Chromium.
**Context:** `/convert/url` is a public, unauthenticated endpoint that fetches
whatever host it is handed *from inside the container*. That is a textbook
server-side request forgery primitive: `http://127.0.0.1:7860/` reaches the
backend's own surface, RFC-1918 addresses reach whatever shares the network, and
`169.254.169.254` is the cloud instance-metadata address. All are reachable from
where the renderer runs and from nowhere the caller sits. Nothing in the code or
the threat model addressed it — ADR-002 chose "open but protected", and rate
limiting is not a control against this.
**Decision:** Resolve the host before anything fetches it and refuse the request
when *any* resolved address is loopback, private, link-local, reserved, multicast
or unspecified (a name answering with both a public and a private record must not
be a coin flip on which one Chrome connects to). Only `http`/`https` are followed,
so `file:`/`ftp:` are out even though `HttpUrl` already blocks them at the API
layer — the parser is also reachable from `monitor.py` and tests. The check raises
a typed `BlockedUrlError`, which `main.py` maps to **400**: this is a caller error,
not a failed render, and a 502 would read as "wiseau is broken".
`WISEAU_ALLOW_PRIVATE_URLS=1` opts a self-hosted deployment back in, because
converting your own intranet is a legitimate use of a self-hosted converter.
**Rejected:** (a) an allowlist of hosts — this is a *universal* ingestion engine;
(b) doing nothing and documenting the exposure — the mitigation is cheap and the
endpoint is public; (c) blocking inside `browser.py` — the guard belongs before the
browser starts, so a probe of the internal network costs no render.
**Consequences:** Deliberately **not** airtight, and it must not be sold as such:
Chrome follows redirects itself, so a public URL that 302s to a private one still
reaches it, and a DNS rebind between the lookup and the render wins. Closing those
needs a proxy-level egress control, which is the right place for it and out of
scope here; this closes the direct case, the only one a caller can trivially aim.
An unresolvable host is allowed through so it fails as an ordinary 502 rather than
a misleading 400. The live-browser tests serve their fixtures over loopback and so
set the opt-out — which is itself the documented way to run against a private
address.

## ADR-020 — repair Trafilatura's duplicated body on short pages in the URL parser
**Date:** 2026-07-26 · **Status:** Accepted — implemented and verified live (real Chromium render, before/after). **Guard rails amended by ADR-022** (the block count never bounded what the repair could damage; the repeat's own size does).
**Context:** A backlog note from the ADR-017 session recorded that Trafilatura
returns the body of very small documents twice. Chasing it to the source: when
Trafilatura's own extraction yields less than `MIN_EXTRACTED_SIZE` (250
characters) of text, `extract_content` calls `recover_wild_text`, which
**extends** the already-populated result body with every `<p>`/`<table>`/… it can
find in the document — including the ones already extracted. So the trigger is not
"tiny document" but *any* page whose extracted text is under 250 characters and
whose main content sits in a container the extractor recognises (`<article>`, a
content `<div>`). Short government notices are exactly that shape, and the
duplicate is invisible to a caller: valid Markdown, plausible content, silently
doubled. It also affects the duplicated run's position — Trafilatura appends
comments after the body, so the repeat is not always a suffix.
**Decision:** Repair it in `url_parser`, after extraction and before
`clean_markdown()`. `_drop_repeated_run` finds the **longest** run of blocks
immediately followed by an identical run and drops the second copy. Three guard
rails keep it from ever touching a healthy document: it only runs on the
Trafilatura path (never markdownify, never the PDF pipeline), only on documents of
≤ 60 blocks (a long page cannot have the upstream bug, and this also bounds the
scan's cost), and only for repeats of ≥ 40 characters, so a genuinely repeated
short line ("Yes", a table cell) is left alone.
**Rejected:** (a) lowering `MIN_EXTRACTED_SIZE` via Trafilatura's config — the
same constant gates the justext rescue *and* several readability-vs-extraction
comparisons, so tuning it to fix duplication silently changes which extractor
serves other pages; (b) deduplicating in `cleaner.py` — the normalizer is shared
by every path and must stay a pure, non-lossy normalizer; (c) Trafilatura's own
`deduplicate=True` — it uses a process-wide LRU cache, i.e. output that depends on
what was converted *before it*, which would break determinism outright.
**Consequences:** Short pages now convert once, deterministically and
idempotently. The repair is lossy in one narrow case by construction — a document
that legitimately repeats a substantial block run back-to-back loses the second
copy — accepted because that shape is rare and the duplication is common in the
target corpus. It is a workaround for upstream behaviour: revisit it when
Trafilatura stops double-counting recovered text, and delete it rather than build
on it.

## ADR-019 — observability is stdlib-only, in-process, and attributes every conversion to an engine
**Date:** 2026-07-26 · **Status:** Accepted — implemented and verified live (real requests, real fallback, real logs).
**Context:** Two blind spots, one of them created on purpose. (1) `MAX_CONCURRENT_JOBS`
defaults to 4 with nothing measured behind it — the number that decides whether a
free-tier container gets OOM-killed was a guess. (2) ADR-014 makes document
conversion docling-first with an automatic fallback, which by design converts a
docling outage into a **successful** response. That is the right behaviour and it
means a dead docling Space is indistinguishable from normal operation: the
fidelity the whole of Phase 6 exists to deliver could be absent for weeks without
a single error.
**Decision:** Add `backend/observability.py`: a JSON-lines log formatter (one
machine-readable object per record, `WISEAU_LOG_FORMAT=text` to opt out) plus a
thread-safe in-process metrics registry, surfaced at `GET /metrics` (API
`0.3.0 → 0.4.0`, additive). Every conversion is attributed to the engine that
actually produced it — `docling`, `pymupdf`, `mammoth`, `ocr`, `trafilatura`,
`markdownify` — and docling's successes, fallbacks (by typed reason), and skips
(not selected vs not configured) are counted apart. Job queue-wait, run duration,
peak concurrency, and process RSS are recorded for sizing. Every request gets a
correlation id, returned as `X-Request-ID` and logged; uvicorn's own access log is
switched off so it does not duplicate the structured one.
**Rejected:** a Prometheus client and an exporter sidecar — a new runtime
dependency and a second thing to deploy, for a two-Space free-tier service where
nobody is running a scraper. Stdlib only, consistent with ADR-010/016; the
counters are an operational aid, not an audit trail.
**Consequences:** `/metrics` is public, so it deliberately carries **aggregates
only** — no URLs, filenames, or content. Numbers are per-process and reset on
restart (a multi-worker deployment reports per-worker slices); that is enough to
answer "is docling serving anything?" and "what should the ceiling be?", and not
enough to be mistaken for a metrics backend. The parsers now import
`observability` directly rather than having a recorder threaded through their
signatures — recording is a global side channel, and it must stay one that cannot
change a byte of output.

## ADR-018 — the docling Space is the *upstream* CPU image, digest-pinned, with two independent guards
**Date:** 2026-07-26 · **Status:** Accepted — image written & digest-verified against the registry; **not built or deployed** (no Docker daemon in the build session).
**Context:** ADR-015 put docling-serve on its own 16 GB HF Space. The roadmap left
the *how* open: build docling-serve from source (as the backend image builds
Chromium in) or start from the project's published image. Two facts decided it.
(1) The upstream `docling-serve-cpu` image already bakes the **model weights** in
(~4.4 GB); a from-source image would download them at boot, and on the free tier
that download would land on the *first request after a cold start* — exactly the
request that then blows past `WISEAU_DOCLING_TIMEOUT` and falls back, every wake.
(2) Reading the upstream docs turned up two auth mechanisms that are **not** the
same thing: docling-serve's own `DOCLING_SERVE_API_KEY` (checked as an `X-Api-Key`
header) and the HF platform's bearer token for a private Space. ADR-015 only
recorded the bearer token, so a deployment following it would have been open to
anyone who guessed the Space URL.
**Decision:** `docling/Dockerfile` starts `FROM
ghcr.io/docling-project/docling-serve-cpu:v1.27.0@sha256:a70cd391…` — tag for
readability, digest for immutability — and only re-homes it for Hugging Face:
port 7860, UID 1000, writable state under `/tmp` (the baked weights are already
world-readable, so no `chown -R` layer that would double the image size), one
worker sharing one copy of the models, and `DOCLING_SERVE_MAX_SYNC_WAIT=100` so
docling's own 504 lands *before* the backend's 120s client timeout. The client now
sends **both** credentials, from separate env vars —
`WISEAU_DOCLING_TOKEN` → `Authorization: Bearer` (gateway),
`WISEAU_DOCLING_API_KEY` → `X-Api-Key` (docling-serve). Neither is baked into the
image. Relatedly, 401/403/429 are re-classified as `DoclingUnavailable`, not
`DoclingBadDocument`: a bad credential or a rate limit is a *deployment* problem,
and logging it as "docling rejected the document" would send the next instance
hunting the wrong bug.
**Consequences:** Pinning the engine bounds the stochastic variation ADR-013
accepts — output can vary run-to-run from the model, but not from an unnoticed
`latest` bump. Upgrading docling is now a deliberate two-line edit (tag +
digest), and the digest must be re-fetched from the registry when the tag moves.
The image is large; expect slow first pulls and a long Space cold start (hence
`DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true`, so warm-up happens at boot rather than
inside a request). **Unverified:** the session that wrote this had no Docker
daemon, so the image has never been built — the digest was checked against the
ghcr registry API, nothing more. First deployer: build it before trusting it.

## ADR-017 — direct-PDF URLs are downloaded through the browser session and routed to the document pipeline
**Date:** 2026-07-26 · **Status:** Accepted — implemented and verified live (real Chromium, locally served PDF).
**Context:** A large share of government "pages" are not HTML — the link resolves
straight to a PDF. Chrome renders those in its built-in PDF *viewer*, whose DOM is
a lone `<embed type="application/pdf">` with no text, so `/convert/url` returned
plausible-looking near-empty Markdown: the worst kind of failure, because it looks
like a success. Meanwhile `/convert/file` gained the whole docling-first pipeline
(ADR-014) that those documents most need. Fetching the bytes with a *second*,
plain HTTP client was the obvious fix and the wrong one: the WAF clearance,
cookies, and TLS handshake that got the browser in do not transfer, so precisely
the sites that need the stealth browser would fail.
**Decision:** After the render, if the DOM is Chrome's PDF viewer *or* the URL
path ends in `.pdf`, download the bytes **from inside the already-navigated page**
(`browser.fetch_bytes` runs `fetch()` via `execute_async_script` and returns
base64), then hand them to `file_to_markdown` — the same docling-first,
auto-fallback, `clean_markdown()`-normalized path an upload takes. The heuristics
only decide whether to *try*; nothing is treated as a PDF unless the bytes start
with `%PDF-`, so a `.pdf` URL that actually serves an HTML consent page falls
through to the normal extraction path. When the viewer is unmistakably present but
the bytes are unreachable, raise (→ 502) rather than return the empty shell.
**Consequences:** `/convert/url` now answers direct-PDF links with real content,
and inherits docling fidelity for them. Cost: `browser.py` grew a transport
responsibility beyond building a driver (it downloads bytes; it still knows
nothing about Markdown), and `url_parser` now imports `file_parser` — a one-way
edge, no cycle. A PDF served through `/convert/url` is *not* deterministic when
docling answers, same as an upload (ADR-013). The download runs in-page, so a site
that blocks `fetch()` from its own origin yields `None` and, if the viewer was
detected, a clean 502 telling the caller to upload the file instead. The one thing
fakes could not answer — whether Chrome's PDF-viewer context can `fetch()` its own
URL — was checked live: it can, and the second request revalidates against the
browser cache (`304`), so the document body is not transferred twice.

## ADR-016 — docling client uses stdlib `urllib`, not `httpx`; typed errors drive fallback
**Date:** 2026-07-26 · **Status:** Accepted
**Context:** Implementing the Phase 6 docling client (ADR-014). The roadmap
assumed an `httpx`-based client ("only an HTTP client — httpx, already present"),
but `httpx` is **not** a backend runtime dependency: it appears only in
`requirements-dev.txt` (for FastAPI's `TestClient`) and `requirements-mcp.txt`
(the separate MCP process). The docling client, by contrast, is imported by
`parsers/file_parser.py` at module load, so a hard `httpx` import would make the
**whole backend fail to import** if the library were ever absent — directly at
odds with the "never hard-depend on the docling Space / degrade to a working
result" resilience rule (ADR-014, invariant-style). `monitor.py` (ADR-010) had
already established a zero-dependency, stdlib-`urllib` HTTP-client precedent in
this same codebase, with an injectable transport for testing.
**Decision:** Build `parsers/docling_client.py` on the **standard library**
(`urllib`), mirroring `monitor.py`: a `convert_document(bytes, filename, ...)`
function with an **injectable transport** so tests mock docling-serve without a
live Space (the roadmap's "mocked transport" requirement). Multipart is hand-encoded
with a content-hash-derived boundary (deterministic, collision-safe). Errors are
**typed** so the caller can distinguish infrastructure problems from bad inputs:
`DoclingUnavailable` (connection error, timeout, 5xx, empty/non-JSON body) and
`DoclingBadDocument` (a 4xx verdict), both subclassing `DoclingError`.
`file_parser.py` gates docling on `WISEAU_PDF_ENGINE=docling` (default) **and**
`docling_client.is_configured()` (`WISEAU_DOCLING_BASE` set); on any `DoclingError`
it logs and falls back to the deterministic PyMuPDF/Mammoth path. So with no
docling base configured — the default local/dev/CI case — behaviour is byte-for-byte
what it was before Phase 6, and every existing test is unaffected. The returned
Markdown is **not** normalized in the client; `file_to_markdown` runs
`clean_markdown()` on it like every other path (invariant #3), inside the existing
`_job_semaphore` (invariant #4, unchanged in `main.py`).
**Consequences:** The backend image acquires **no** new runtime dependency
(`requirements.txt` untouched — still no torch, no httpx) and can never fail to
import for lack of an HTTP library; the docling call path is fully unit-tested over
a mocked transport (18 client tests + 6 engine-selection tests, browserless and
docling-serve-less). Cost: hand-rolled multipart instead of `httpx`'s helper (a
dozen lines, tested). This ADR refines — does not overturn — the roadmap's client
task; the "httpx" note there was inaccurate for the runtime image. Still open for
Phase 6: routing PDF-typed `/convert/url` fetches to docling, the docling-serve
Space (ADR-015), and live upload/fallback verification.

## ADR-015 — docling-serve as an internal microservice; free two-Space deployment
**Date:** 2026-07-24 · **Status:** Proposed (implementation is Phase 6)
**Context:** ADR-014 makes docling the default document parser, and docling must
be **self-hosted** to stay free of per-page fees. docling is PyTorch + model
weights (~2–4 GB RAM to load and run), and the existing WAF-bypass path is a
headless Chromium (also RAM-hungry). Of the free tiers surveyed, only **Hugging
Face Spaces free** (2 vCPU / **16 GB** RAM, Docker) has enough memory for either;
**Render free** (512 MB) OOMs on both PyTorch *and* real-page Chromium and is
usable only for static hosting. The owner wants a **live paste/upload → convert**
experience for $0.
**Decision:** Deploy three cooperating pieces, decoupled over HTTP (consistent
with ADR-004/009/010):
1. **Static UI** — GitHub Pages (existing Phase 5 plan). `config.js`
   `MARKDOWN_API_BASE` → the wiseau backend Space.
2. **wiseau backend + headless Chrome** — HF Space #1. The public front door:
   owns the `MarkdownResponse` contract, the fair-use guards, and the
   WAF-bypass fetch. Not a model box.
3. **docling-serve** — HF Space #2. The internal converter. Called **only** by
   the wiseau backend over HTTP (`WISEAU_DOCLING_BASE`), never by the public
   directly. Protect the internal call with a shared secret
   (`WISEAU_DOCLING_TOKEN`, bearer header) and/or a private Space.
Pre-download the docling model weights **at image build** (`docling-tools models
download`) so a cold start doesn't also pay a download and so it runs with
restricted egress; **pin docling-serve + the model revision**. docling-serve
runs its own low concurrency (1–2 on 2 vCPU); the wiseau side uses a bounded
timeout and falls back (ADR-014) on cold-start/overload.
**Consequences:** A $0, memory-isolated, live stack that matches the project's
decoupled architecture; each box scales on its own 16 GB / 2 vCPU. Costs to
accept: two Spaces to operate; **CPU-only inference is slow** (seconds/page) and
free Spaces **sleep after ~48 h idle** (30–60 s+ cold start) — both softened by
the automatic fallback and an optional warm-ping. The datacenter IP is unchanged,
so the **WAF ceiling is unchanged** — a residential proxy is the (paid) escape
hatch for the most aggressive targets and is explicitly out of scope here. The
WAF-bypass fetcher itself is a separate concern (a `nodriver`/`undetected-
chromedriver` upgrade to `browser.py`), tracked independently of this ADR.

## ADR-014 — docling as a document parser; PyMuPDF/Mammoth as automatic fallback
**Date:** 2026-07-24 · **Status:** Accepted — backend implemented 2026-07-26 (client + engine selection + fallback); docling-serve Space & live verification still pending. See ADR-016. **Amended by ADR-027 (2026-07-30): docling is no longer the *default* engine — it is selected per deployment or per request. The engine layer, the fallback, and everything else below stand as written.**
**Context:** docling (layout model + TableFormer + integrated OCR) produces
markedly more **faithful** Markdown on complex, multi-column, and scanned
documents — notably the government PDFs this project targets — than the legacy
PyMuPDF4LLM path, which ADR-012 explicitly pinned to `use_layout(False)` and so
"forgoes the newer engine's richer table handling." With ADR-013 relaxing strict
determinism, docling can now be the default. But docling is heavy and, on the
free HF CPU tier, slow with cold starts (ADR-015), so it can be **transiently
unavailable** — a hard dependency would make the whole service flaky.
**Decision:** Make document conversion **docling-first with automatic fallback**,
implemented as a pluggable parser-engine layer that mirrors `parsers/ocr.py`:
- New `parsers/docling_client.py` — a thin HTTP client to docling-serve at
  `WISEAU_DOCLING_BASE` (mirrors the `WISEAU_API_BASE` pattern of the MCP server
  / monitor). Sends the document bytes, requests `md`, returns Markdown; bounded
  timeout, typed errors.
- `parsers/file_parser.py` selects the engine via `WISEAU_PDF_ENGINE`
  (default `docling`; `docling` | `pymupdf`). On a connection error, timeout,
  5xx, or empty result from docling, **log and fall back** to the existing
  deterministic parsers (PyMuPDF4LLM + OCR for PDF/image; Mammoth for DOCX).
- All output still ends in `clean_markdown()` (invariant #3 unchanged), and the
  docling call runs inside the existing `_job_semaphore` (invariant #4 unchanged).
- DOCX stays on Mammoth by default (cheap, already deterministic); route it to
  docling only when `WISEAU_PDF_ENGINE=docling` is set *and* docling is up.
**Consequences:** High fidelity when docling is up; graceful degradation to a
working (lower-fidelity, deterministic) result when it is cold/asleep/down —
essential on the free tier. Costs: two conversion paths to maintain; the fallback
can **mask** docling outages, so log/metric which engine served each request
(feeds the observability backlog item). Whether a given conversion is
deterministic now depends on which engine answered — accepted under ADR-013. The
public `MarkdownResponse` contract is unchanged (no version bump for the shape;
this is an engine swap behind it).

## ADR-013 — Fidelity over strict determinism for the docling extraction path
**Date:** 2026-07-24 · **Status:** Accepted · **Amended by ADR-027 (2026-07-30):** the relaxation below applies to the docling path, which is now *opted into* rather than defaulted to; a stock deployment is deterministic end-to-end again. docling's run-to-run variation is still intended, and must not be "fixed".
**Context:** Invariant #1 ("Determinism is the product") made byte-identical
output the core guarantee, which drove algorithmic extraction (Trafilatura,
ADR-001), the legacy-mode PyMuPDF pin (ADR-012), and kept ML engines opt-in
(EasyOCR). The project owner has **reprioritized**: the goal is faithful Markdown
of real-world documents (government PDFs with complex tables and scans), and a
**stochastic** ML extractor is acceptable — preferred, even — when it is more
faithful than a deterministic one.
**Decision:** Relax invariant #1 from an absolute to a **scoped** guarantee.
Fidelity now outranks reproducibility for the default document path. The
deterministic paths that remain deterministic — `clean_markdown()` normalization,
the PyMuPDF/Mammoth fallback (ADR-014), and Trafilatura URL extraction — keep
that property; the new docling default is **best-effort / high-fidelity** and may
vary run-to-run. `clean_markdown()` still runs on every path (invariant #3
untouched).
**Consequences:** Unlocks docling as the default (ADR-014). Costs to accept:
(a) the same input may yield slightly different Markdown across runs on the
docling path; (b) the autonomous-ingestion **monitor** (ADR-010) will see
conversion noise as spurious `changed` results on that path — treat monitor
diffs as best-effort when docling served the conversion, or diff a tolerant/
normalized baseline; (c) any future "same input → same output" response cache is
invalid for the docling path. **Guidance for future instances: do NOT "fix"
non-deterministic docling output as a bug — it is intended.** tech-spec §1
(invariant #1) is amended to reflect this scope.

## ADR-012 — OCR for scanned/handwritten PDFs and images (deterministic, pluggable)
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** The engine only read a PDF's embedded text layer (`pymupdf4llm`), so
scanned and handwritten PDFs — which are just page images — converted to empty
Markdown. OCR was needed, and it had to honour invariant #1 (determinism) and
stay light enough for the free Hugging Face CPU tier the backend still has to be
deployed to. Investigation surfaced two traps in `pymupdf4llm` 1.28:
1. Its **new layout engine** (`use_layout(True)`, the default) accumulates
   cross-call process state that *non-deterministically drops content* — not just
   OCR text but sometimes a page's native text — once several varied documents
   pass through one worker. Fatal for determinism.
2. Its **built-in OCR integration** has the same instability (progressive
   word-dropping across calls). MuPDF's own OCR primitive
   (`Page.get_textpage_ocr`), by contrast, is clean and byte-reproducible.
**Decision:** Do OCR ourselves, deterministically, and route around both traps:
- Pin `pymupdf4llm.use_layout(False)` (stable legacy extractor) for native text.
- Detect image-only pages **per page** (`< 16` non-whitespace chars of embedded
  text); OCR only those, then assemble the document in page order. A fully
  digital PDF keeps the exact pre-OCR fast path; a fully scanned PDF is entirely
  OCR'd; mixed PDFs interleave. Image uploads (PNG/JPEG/TIFF/...) are re-wrapped
  as a one-page PDF and OCR'd the same way — new supported input types.
- Make the OCR engine **pluggable** (`parsers/ocr.py`). Default **tesseract**
  (MuPDF's built-in Tesseract via `get_textpage_ocr`; system binary only, no new
  Python dep, deterministic, strong on printed/scanned). Opt-in **easyocr**
  (`WISEAU_OCR_ENGINE=easyocr` + `requirements-ocr.txt`) is a neural engine that
  handles handwriting/noisy captures; PyTorch is kept out of the default image.
- Configuration via env: `WISEAU_OCR_MODE` (`auto`/`force`/`off`),
  `WISEAU_OCR_DPI` (fixed 300 for reproducibility), `WISEAU_OCR_LANG`,
  `WISEAU_OCR_ENGINE`. Output still flows through `clean_markdown` (invariant #3)
  and runs under the concurrency + rate-limit guards (invariant #4). API `0.2.0
  → 0.3.0` (additive: new input types, no response-shape change).
**Consequences:** Scanned/handwritten documents now convert. The default stays
tiny and deterministic (only `tesseract-ocr` + `tesseract-ocr-eng` added to the
image; tessdata is auto-discovered — no `TESSDATA_PREFIX` hardcode that could go
stale across distros). CI installs Tesseract so the OCR tests run for real, and
the `docker-build` job OCRs a page *inside the image* to prove the deployed
container can. Watch for: (a) genuine handwriting is only "very good" with the
EasyOCR engine — Tesseract alone is weak on cursive, and no self-hosted engine is
flawless; (b) if a future `pymupdf4llm` fixes the layout-engine instability,
revisit the `use_layout(False)` pin (it also forgoes the newer engine's richer
table handling); (c) OCR is memory/CPU-heavy at 300 DPI — it shares the existing
`_job_semaphore`, so tune `MAX_CONCURRENT_JOBS` if scans dominate traffic.

## ADR-011 — Docker image verified end-to-end; CI builds it and renders a live URL
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** Every prior session was blocked on the same two things: no Docker
daemon to build the image, and an egress proxy headless Chrome couldn't consume,
so a *live external* URL had never rendered through the containerized pipeline.
This session's environment had both a working Docker daemon and direct egress,
making the marquee Phase 5 / last-of-Phase-2 verification finally possible. One
sandbox wrinkle remained: outbound HTTPS is re-terminated by an egress gateway
presenting its own CA, which Chromium's NSS store doesn't trust by default (a
render returns the browser's "connection is not private" interstitial as the
page — proving the pipeline works, but not on real content).
**Decision:** Build the committed `backend/Dockerfile` and verify the whole path
inside the container — `/ping`, Chromium **150** + ChromeDriver **150** (matched
pair from apt), and `POST /convert/url` on real external URLs. The production
Dockerfile was kept clean; sandbox-only CA trust (pip build egress + a per-user
NSS import of the egress CA so Chromium renders real content) was applied through
a *throwaway* `Dockerfile.verify` / NSS import that were **deleted after
verification, not committed**. To make this a standing guarantee rather than a
one-off, add a `docker-build` job to `backend-tests.yml`: it builds the image,
starts the container, asserts `/ping`, checks the Chromium/ChromeDriver versions,
and does a real `POST /convert/url` on `https://example.com` asserting the
extracted `Example Domain` content — GitHub-hosted runners have a Docker daemon
and *direct* egress (no gateway), so no CA workaround is needed there.
**Consequences:** The image is proven to build from pinned deps and to render
real external pages deterministically (example.com → clean Markdown; a Wikipedia
article → ~30 KB of structured Markdown; byte-identical SHA-256 across two runs).
The container is deployment-ready for Hugging Face Spaces. CI now guards the heavy
browser path the unit suite mocks, closing the last cross-cutting test gap. Still
open (needs external accounts/credentials, not code): the actual HF Space deploy,
pointing `frontend/config.js` at it, and GitHub Pages — the remaining Phase 5
items. The one non-obvious gotcha for future local runs *in this sandbox*: a live
external HTTPS render returns the egress gateway's TLS interstitial unless the
gateway CA is imported into Chromium's NSS store (`~/.pki/nssdb`); this does not
apply to production or CI.

## ADR-010 — Autonomous ingestion as a zero-dependency HTTP client of the backend
**Date:** 2026-07-22 · **Status:** Accepted
**Context:** The last open Phase 4 item is an autonomous-ingestion example:
scheduled diff-checking of a URL's Markdown against a saved snapshot. Two design
choices had to be settled. (1) *How does it reach the engine?* Mirroring ADR-009,
it could import the parsers in-process or hit the HTTP API — and invariant #4
(fair-use guards are unconditional) forces the same answer: go over HTTP so it is
rate-limited and concurrency-capped like any other client. (2) *What is a
"change"?* Determinism is per-input, not across time (tech-spec §7): a live page
legitimately drifts, so a content difference is an *expected, reportable outcome*,
not a failure — only being unable to obtain fresh Markdown (backend down / render
error) is an error.
**Decision:** Add `backend/monitor.py` as a **thin HTTP client** over
`POST /convert/url` at `WISEAU_API_BASE`, using **only the Python standard
library** (`urllib`, `difflib`, `hashlib`, `json`, `argparse`) — no new
dependency to install or pin, and it runs anywhere the backend URL is reachable.
A `SnapshotStore` persists the last-seen Markdown per URL as one JSON file
(`WISEAU_SNAPSHOT_DIR`, default `.wiseau-snapshots`). `check_url` returns a typed
`CheckResult` with status `new` (first sight → baseline saved), `unchanged`
(identical content), `changed` (a deterministic unified diff attached — content
drift, `ok` is still true), or `error` (fetch failed; the last good baseline is
left untouched so the next check diffs against it). A CLI runs a single pass or,
with `--watch --interval N`, a bounded/looping schedule; the diff is dateless so
the same before/after pair always yields the same output.
**Consequences:** One extraction path and one contract, guards always in force,
zero added dependencies. The monitor needs a reachable backend (documented, same
as the MCP server). Tested with an injected fake fetcher and a real `urllib` path
against a stdlib stub server (new/unchanged/changed/error, exit codes, bounded
watch loop) — 16 tests, no browser or external network. Scheduling itself (cron,
systemd timer, CI) is left to the operator; `--watch` is a self-contained example.
Completes Phase 4.

## ADR-000 — Establish the documentation foundation for an instance chain
**Date:** 2026-07-20 · **Status:** Accepted
**Context:** The project is executed by a series of Claude Code instances with no
shared memory beyond the repo. Without durable, role-separated docs, each
instance re-derives context and drifts off-spec.
**Decision:** Maintain six documents with distinct roles — `CLAUDE.md` (entry
point/rules), `STATUS.md` (live state), `project-brief.md` (vision),
`tech-spec.md` (contract), `roadmap.md` (task backlog), `decisions.md` (this
log) — plus `agent-workflow.md` (handoff protocol). Facts-vs-behaviour-vs-
rationale-vs-todo are kept in separate files to avoid overlap and staleness.
**Consequences:** Each instance has one predictable place to look and to update.
Cost: the docs must be kept current — enforced by the "update STATUS.md before
you finish" rule in `CLAUDE.md` and `agent-workflow.md`.

## ADR-001 — Deterministic algorithmic extraction over per-site selectors
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Content extraction can be done with hand-tuned CSS selectors per
site or algorithmically. Selectors are precise but brittle and non-portable.
**Decision:** Use Trafilatura for main-content extraction, with markdownify only
as a fallback when Trafilatura returns nothing. No per-site selectors.
**Consequences:** Robust across arbitrary layouts and the core of the determinism
guarantee. Some sites may extract imperfectly; that is accepted over brittleness.
Adding per-site logic would violate this ADR — open a new one if ever needed.

## ADR-002 — Open, public API guarded by fair-use limits, not origin locks
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** The API serves the hosted UI, forks, third-party frontends, and
agents. Locking CORS to one origin would block legitimate reuse.
**Decision:** Permissive CORS (`allow_origins=["*"]`, no credentials). Protect the
shared compute with per-IP rate limiting (`slowapi`) and a global concurrency
ceiling (`asyncio.Semaphore`) rather than access control.
**Consequences:** Anyone can call it ("pay it forward"). Abuse risk is real;
first defence is rate limiting, with quotas/API-key tiers held in reserve (brief
§7). Every heavy endpoint must stay behind both guards.

## ADR-003 — Single response contract for humans and agents
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Two consumer classes (UI, LLM/agents) could each get a tailored
response shape.
**Decision:** One `MarkdownResponse` (`source`, `markdown`, `length`) for both,
described by the auto-generated OpenAPI schema.
**Consequences:** One thing to maintain and version; MCP/agent integration reuses
it directly. Consumer-specific needs must be met additively, not by forking the
contract.

## ADR-004 — Decoupled static frontend + containerized backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** UI and engine have very different hosting and iteration needs.
**Decision:** Static frontend on GitHub Pages; containerized FastAPI backend on
Hugging Face Spaces; they communicate only over HTTP. The frontend's sole backend
coupling is `MARKDOWN_API_BASE` in `config.js`.
**Consequences:** Each layer deploys and evolves independently; free hosting on
both tiers. No build-time coupling is permitted between them.

## ADR-005 — Version-locked Docker image for the backend
**Date:** 2026-07-20 · **Status:** Accepted (from project brief)
**Context:** Headless Chromium extraction is sensitive to system-library and
browser versions; "works on my machine" drift would break determinism.
**Decision:** Ship a `Dockerfile` (Python 3.11-slim + system Chromium/chromedriver)
that version-locks the runtime; run as non-root UID 1000 for Hugging Face.
**Consequences:** Reproducible runtime. *Follow-up (resolved 2026-07-20):* Python
dependencies in `requirements.txt` are now version-pinned — see ADR-006 —
completing this decision's intent.

## ADR-009 — MCP server as a thin HTTP adapter over the backend
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** Phase 4 calls for an MCP server wrapping `/convert/url` and
`/convert/file` as agent tools. Two implementations were possible: (a) import the
`parsers` functions in-process and run extraction inside the MCP process, or (b)
make the MCP server an HTTP client of the running backend. Option (a) is one
fewer moving part but bypasses the rate limiter and concurrency ceiling, which
tech-spec invariant #4 says are *unconditional* for every heavy conversion, and
would create a second place the extraction stack is wired up (drift risk).
**Decision:** Build `backend/mcp_server.py` as a **thin HTTP adapter** (option b)
using the MCP SDK's `FastMCP`. Each tool (`convert_url`, `convert_file`, `ping`)
issues an HTTP request to the backend at `WISEAU_API_BASE` (default
`http://localhost:7860`), so it reuses the exact `MarkdownResponse` contract and
inherits the fair-use guards unchanged — mirroring the frontend's single
`MARKDOWN_API_BASE` coupling (ADR-004). Backend error `detail` is surfaced
verbatim. `convert_file` reads a local path (the MCP server runs alongside the
agent) and forwards bytes + filename so the backend still dispatches by
extension. Also refined the OpenAPI surface: explicit operation IDs
(`convert_url`/`convert_file`/`ping`) + per-route summaries so `/openapi.json`
reads cleanly as a function-calling schema; bumped the API version to `0.2.0`.
The MCP SDK is an optional extra (`requirements-mcp.txt`), added to
`requirements-dev.txt` so CI can test the adapter; `httpx` was already a dev dep.
**Consequences:** One extraction path, one contract, guards always in force. The
tools require a reachable backend (documented) — acceptable, and consistent with
the decoupled architecture. Tested with a mocked `httpx` transport (no live
server/browser) and verified end-to-end against a live `uvicorn` backend
(`ping`, real-PDF `convert_file`, 415 error path). Remaining Phase 4 item:
autonomous ingestion (scheduled diff-checking). See `docs/mcp.md`.

## ADR-007 — Live browser path verified; real DOCX round-trip added; live test kept opt-in
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** After ADR-006, the two biggest automation gaps were the live
Chromium render path (the project's biggest unknown) and a real DOCX-body
round-trip. The live path was verified end-to-end this session: with a
version-matched Chromium + chromedriver, `url_to_markdown` launches headless
Chrome, renders the DOM, and Trafilatura → cleaner produce clean Markdown.
Two practical findings: (1) chromedriver must match the Chrome *major* version —
a mismatched driver on `PATH` fails to start a session; Selenium Manager
(`selenium-manager --browser chrome --browser-version <N>`) auto-fetches the
matching pair, and `browser.py` already honours `CHROME_BIN`/`CHROMEDRIVER_PATH`
for explicit pinning. (2) This CI/sandbox forces outbound HTTPS through an
authenticated proxy that headless Chrome cannot consume via `--proxy-server`, so
fetching *arbitrary external* URLs is blocked *here* — not a code defect; the
Docker/Spaces deployment has direct egress.
**Decision:** Keep the live-browser test **opt-in** — a new
`tests/test_browser_live.py`, skipped unless `WISEAU_LIVE_BROWSER=1`, exercising
the full render → extract → clean pipeline against a self-contained `data:` URL
(no network) so it is repeatable without egress. Default CI stays browser-free.
Add real DOCX round-trip tests (dispatch, structure, determinism, and an HTTP
happy-path) using in-memory `python-docx` fixtures; pin `python-docx` in
`requirements-dev.txt`.
**Consequences:** Phase 2's render path is proven and codified as a runnable test
rather than a one-off manual check; the DOCX-body path is now covered by
automation. CI remains fast and browserless. To run the live test in a new
environment, ensure a matching Chrome/chromedriver (Selenium Manager handles this
by default) and set `WISEAU_LIVE_BROWSER=1`. Verifying live *external* URLs still
requires an environment with direct egress (e.g. the Docker image, Phase 5).

## ADR-006 — Browser-free deterministic test suite + pinned dependencies
**Date:** 2026-07-20 · **Status:** Accepted
**Context:** The engine had no tests and no CI, and its dependencies were
unpinned — both flagged risks to the determinism guarantee. The heavy path (URL
rendering) needs Chromium, which is slow and awkward to provision in CI, but the
load-bearing determinism logic (`cleaner`, file dispatch, request validation)
does not.
**Decision:** Test the browser-free half directly and mock the Chromium worker
(`url_to_markdown`) so the `/convert/url` route's plumbing is still covered
without a browser. PDF paths use in-memory PyMuPDF-generated documents, so no
fixture files are needed. Pin every direct dependency in `requirements.txt` to
the versions the suite was verified against; keep test-only tools in
`requirements-dev.txt`. CI (`.github/workflows/backend-tests.yml`) installs both
and runs `pytest` on any `backend/**` change — no browser provisioned.
**Consequences:** Fast, deterministic CI that guards the contract on every push.
The live browser-render and DOCX-body paths remain unproven by automation and
still need manual/Docker verification (tracked in `roadmap.md`). Dependency bumps
are now deliberate: change the pin, re-run the suite.

## ADR-008 — Frontend (Phase 3) verified by driving the real UI against a live backend
**Date:** 2026-07-21 · **Status:** Accepted
**Context:** The static frontend (`index.html`/`app.js`/`config.js`) was
code-complete but had never been exercised against a running backend, so Phase 3's
two verification tasks were still open. The decoupling rule (frontend ↔ backend
only over HTTP via `MARKDOWN_API_BASE`) means the only honest way to mark them
`[x]` is to actually load the page in a browser and watch it talk to the API.
**Decision:** Verify by driving the shipped UI end-to-end with headless Chromium
(Playwright) against a locally-running stack — `uvicorn` backend on `:7860`, the
static frontend served on `:8000`, and a throwaway fixtures server on `:8001` so
the URL path renders a *local* article page (no external egress needed here). The
driver script asserts the observable contract: status badge → `online`, URL
conversion (headless-Chrome render → Trafilatura → cleaner → output pane with a
char-count meta), PDF **and** DOCX upload conversion, copy (clipboard content
checked) and download (`converted.md`), and error rendering for a backend 415 plus
the client-side empty-URL guard. 18/18 checks passed. The driver is a
verification artifact, not committed app code — the frontend has no build step and
we keep it framework-free (ADR-004).
**Consequences:** Phase 3 is proven, not just written; the shared response
contract is confirmed to render correctly for both success and error paths in a
real browser. The check is reproducible in any environment with a browser but is
not wired into CI (the static UI has no test runner and CI stays browserless per
ADR-006); re-running it is a manual step. Live *external*-URL conversion through
the UI remains gated on an environment with direct egress (Phase 5 / Docker),
same as the backend.
