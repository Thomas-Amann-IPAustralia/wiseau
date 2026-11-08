# Roadmap: OCR methodology and document structure (2026-11-08)

A prioritised plan built from two external reviews:
[`Wiseau OCR methodology review`](Wiseau%20OCR%20methodology%20review) and
[`Document structure suggestion`](Document%20structure%20suggestion). The first
copies in `docs/` stopped at about 80%; both now hold the full text from their
ChatGPT share pages (citation markers removed, links restored). The 2026 papers
both reviews lean on all resolve on arXiv: 2611.04874, 2606.13108, 2605.22100,
2608.15064.

**Target corpus.** The Plant Breeder's Rights journals (`pdf-test/`) are the
primary target, though not the only one. So each fix is *tuned and judged* on
held-out PBR pages, and the other material (set E) is a **do-no-harm check**: a
fix ships only if it helps PBR and does not regress set E.

**This is a proposal, not a decision.** Nothing here is built. Each item that
changes behaviour gets its own ADR when it ships. The standing rules still apply:
no paid APIs, references are blind and frozen before an engine runs, nothing
ships without a held-out set, the default path stays deterministic, docling
remains opt-in and has a fallback, and a heuristic refuses rather than guesses.

## The two reviews in one paragraph each

**OCR review.** ADR-038 showed that much of the remaining error comes from *how
text is arranged*, not from reading the characters. So invest in the stages
around recognition: image preprocessing, specialist layout and table detection,
and region-level (not page-level) routing. Separate the metrics for recognition,
order and tables, and test on documents from outside one journal family. Don't
run another voting experiment, and don't use an LLM to "correct" OCR.

**Structure review.** Don't go straight from OCR text to Markdown. Rebuild the
document first as an intermediate representation (IR): elements with page,
bounding box and provenance, plus their relationships (parent, caption-of,
footnote-for, continues-from). Then render Markdown from it with a deterministic
serializer. Recover heading hierarchy, cross-page paragraphs and tables,
figure/caption links, footnotes and page furniture as separate problems, and
measure structure separately from text. Keep the source text unchanged:
`cleaner.py` should stop rewriting punctuation.

## Where the reviews meet the repo

The reviews read the repo from outside, and four points change the priority
order:

- **Hidden OCR layers and set E are already open items** (roadmap Phase 13,
  handover 2026-11-06). The reviews confirm them; they are not new.
- **Word recall that ignores order (bag-of-words) is already measured** (`bow_r`
  / `bow_p` in `score.py`), so the gap between WER and `bow_r` already shows
  order errors. Still missing: CER, an explicit reading-order metric, and table
  structure (TEDS).
- **PP-DocLayout has been partly tested already.** It is GLM-OCR's layout stage
  (V3), and it merged V16 p90's two side-by-side tables into one region. A
  specialist layout model is worth testing, but this evidence says it alone won't
  fix side-by-side tables.
- **docling is a template for the IR, not its source.** The structure review
  would build the IR on docling's JSON, but docling is opt-in (ADR-027) and our
  weakest engine on scans (WER 0.278). The IR has to be fed by the default
  PyMuPDF/Tesseract path first. `DoclingDocument` is the model to copy.
- **The scorer ignores punctuation** (`score.py` lowercases and keeps word
  characters only). Changing the cleaner will not move any OCR score, and the
  benchmark also can't see punctuation fidelity. Both points matter below.

## Priorities

Ordered by value for effort. Gates (a step that must finish before a later one
can ship) are noted inline.

### Tier 0 — small, independent, do first

- [ ] **0.1 Stop rewriting punctuation in `cleaner.py`.** Keep curly quotes, en
  and em dashes and `…` as printed. Keep NFC and line-ending normalisation.
  Preserve a trailing two-space hard break. Offer the old behaviour as an opt-in
  `simplified` output. This changes the default output for every consumer, so it
  needs **user sign-off**, an ADR, a `tech-spec.md` update and an API version
  bump. Check `chapters.py` contents matching and `keywords.py` against curly
  punctuation before shipping. *Effort: small.*
  **Recommendation (2026-11-08): do it, narrowly.** Drop only the
  quote/dash/ellipsis rows of `_CHAR_REPLACEMENTS`. Keep the NBSP, zero-width and
  BOM rows: those characters are invisible, so removing them loses nothing a
  reader can see. Defer the `simplified` option and the hard-break change until a
  consumer asks. The born-digital PBR volumes (V16, V20) carry real typographic
  punctuation that is flattened today. The change stays deterministic and moves
  no OCR score. *Awaiting the user's go-ahead.*
- [ ] **0.2 Text-layer trust check (Vol 1 No 3).** Blind-transcribe a few pages
  from that PDF, including its table page. Compare the hidden Acrobat layer with
  a fresh OCR pass. If the layer loses, add a gate: an invisible text layer
  (render mode 3) over a full-page image gets re-OCR'd when it disagrees with
  fresh OCR, or when it looks wrong (coverage, replacement characters, column
  read top-to-bottom). The handover's quick check found 99% word agreement on
  prose but 82% on a table page. *Effort: small.*
  **Scope grew (2026-11-08):** five of the six set E PDFs are this same case,
  145 pages of patent-specification scans with an invisible text layer and no
  producer tag. A first look at their layers shows misreads such as `PATENT
  SPEOIFIOATION` and `L-d1ow`. That makes this the most common failure in the
  new material, so do it before any other Tier 1 item.
- [ ] **0.3 Harness metrics.** Add CER, a block reading-order score
  (normalised edit distance over block order), seconds/page, peak RSS and
  failure rate to `benchmarks/bakeoff/`. Add a punctuation-aware variant so 0.1
  is measurable. Report changes only, no new runs needed. *Effort: small.*
- [ ] **0.4 Keep docling's JSON.** Have `docling_client.py` request
  `to_formats=md,json` and keep the JSON next to the Markdown in experiments, so
  the document tree and table spans aren't thrown away. Also check whether the
  pinned docling-serve version exposes the heading-level stage (bookmarks →
  numbering → typography). No contract change yet. *Effort: small.*

### Tier 1 — collect evidence before building more heuristics

- [ ] **1.1 Set E from different publications.** The PDFs are supplied
  (2026-11-08, `pdf-test/Extra-4-Broad-Test/`): five Australian patent
  specifications from 1963 to 2000 (scans with hidden OCR layers, 145 pages) and
  one WO publication (`WO_2006070397_A1`, 21 image-only pages). These are typed
  and printed legal documents, not journals, so they test generalisation and
  hidden-layer handling. Same protocol as before: seeded draw, blind references,
  committed before any engine runs. Exclude near-empty pages and
  microcopy-resolution test charts (for example `1985043407` p1). *Effort:
  moderate (mostly transcription).*
- [ ] **1.1b Held-out PBR pages for PBR-specific fixes.** The gutter-stitch fix
  (V5N3 p24) and the side-by-side-table fix (V5N1 p18) are PBR failures, so they
  must be judged on PBR pages nobody has tuned against, with set E as the
  do-no-harm check. Vol 5 No 1 and No 3 have 46 unused image-only pages (15
  near-empty, and only one table-heavy page left). More PBR scans, especially
  table-heavy ones, would make this held-out set worth having.
- [ ] **1.2 Preprocessing ablation.** Run four variants of each page: the
  original, deskew + contrast/background normalisation, Sauvola thresholding,
  and light denoise. Score each through the current default, broken down by page
  type, and log skew, contrast, blur and noise per page. Keep the original as a
  candidate: only a per-page rule that beats it on held-out pages ships. OpenCV
  only. *Effort: low.*
- [ ] **1.3 Specialist table and recogniser bake-off.** Add Table Transformer
  (PubTables-1M) and PP-StructureV3's table module as harness engines. Run them
  on the known failure pages: V5N1 p18 side-by-side tables, V5N3 p24 directory,
  V16 p90, V2 p17 typewriter table, narrow-column, merged-cell and
  short-first-column tables. Add PP-OCRv6 (small model, CPU) as a recogniser
  next to Tesseract. Record memory, since PaddlePaddle must fit a 16 GB CPU
  Space. *Effort: moderate.*

### Tier 2 — the architectural change

- [ ] **2.1 Canonical document IR.** A JSON tree with elements (title,
  heading+level, paragraph, list/item, table with cell spans, figure, caption,
  footnote, equation, header/footer, page number) and relationships (parent_of,
  caption_of, footnote_for, continues_from, continues_table, references,
  source_for). Every element carries page, bounding box, the source engine, and
  the original text, which is never edited. Recovered structure goes in separate
  fields with a `confidence`, so nothing overwrites the transcription. Adapter
  order: PyMuPDF text layer and the Tesseract layout path first (the defaults),
  then docling JSON, Mammoth, and the URL path. *Effort: large.*
- [ ] **2.2 Deterministic Markdown serializer.** IR → Markdown. **Gate: its
  output must match today's Markdown byte for byte before it replaces the current
  path**, so the refactor is provably behaviour-neutral. After that, adopt an
  explicit dialect: CommonMark plus selective HTML. Simple tables use pipe
  syntax; tables with merged cells or nested headers use HTML. That costs no
  WER, because the scorer strips HTML tags. *Effort: moderate.*
- [ ] **2.3 Structural gold set and metrics.** Annotate 10–15 documents for
  structure, not just text. For every element record its type, text, parent,
  order, page and relations. Include a multi-column journal, a deeply nested
  report, tables that continue across pages, a footnote-heavy document and a
  complex contents page. Metrics: block F1, reading-order edit distance, heading
  tree edit distance, TEDS for tables, relation F1 for captions and footnotes,
  and merge/split accuracy across pages. *Effort: moderate.*

### Tier 3 — structure recovery on top of the IR (each behind 2.3 and set E)

In this order, each reporting its signal and confidence, and refusing when
unsure (ADR-030):

- [ ] **3.1 Heading hierarchy.** Signals in order: PDF bookmarks (`get_toc`),
  then `chapters.py` contents matching extended to *nested* entries, then
  numbering patterns (PART / 1. / 1.1 / (a) / (i)), then typography.
- [ ] **3.2 Page furniture.** Detect running headers, footers and page numbers
  that repeat across pages. Suppress them in reader mode; keep them as
  page-level annotations in faithful mode.
- [ ] **3.3 Paragraph continuation.** Join paragraphs across pages and columns
  using end punctuation, indentation, style, position and section. A line-end
  hyphen is kept in the text, and joining across it is a recorded operation,
  never a silent rewrite.
- [ ] **3.4 Figures as assets.** Export images as separate files with captions,
  sources and figure numbers attached, instead of `image_export_mode=placeholder`.
  This needs a contract decision on how assets travel (ADR-024 bans only *inline*
  base64). It is the first step towards a ZIP of `document.md` +
  `document.json` + `assets/`.
- [ ] **3.5 Footnotes and cross-references.** Link each marker to its note, and
  render with `[^n]`.
- [ ] **3.6 Cross-page tables.** Merge table fragments and drop repeated header
  rows, with an image fallback when the structure can't be rebuilt.
- [ ] **3.7 Region-level routing.** Once a layout detector feeds IR regions,
  send prose to Tesseract and tables to the table pipeline, and re-OCR only
  regions that look suspicious (low calibrated confidence, missing rows,
  uneven column lengths). This replaces page-level routing, which failed its
  held-out test on set C.

### Not now

- Another multi-engine voting experiment (already shown worse than the best
  single engine).
- LLM-based reformatting or post-OCR correction of the transcription. A model
  may *label* structure, but it never rewrites text.
- A visual inspection tool that links each Markdown block back to its source
  region. It is valuable, but it needs 2.1 first. Revisit after Tier 2.

## Suggested sequence

1. Tier 0 together, about one session. Item 0.1 waits for the user's sign-off.
2. Draw and blind-transcribe set E (1.1), and assemble the held-out PBR pages
   (1.1b). Meanwhile, run 1.2 and 1.3 on sets A–D as development, not as
   evidence.
3. Build 2.1 and 2.2 as a byte-for-byte-neutral refactor, then 2.3.
4. Tier 3 in the order listed, each held out on set E and the structural gold
   set.
