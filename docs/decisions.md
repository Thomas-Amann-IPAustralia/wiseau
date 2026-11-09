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

## ADR-046 — Table Transformer reads scanned tables cell by cell; opt-in, refuses prose
**Date:** 2026-11-09 · **Status:** Accepted as opt-in (`WISEAU_TABLES=tatr`); whether to turn it on in the deployed Space is the user's call (roadmap 3.7; user asked for it)
**Context:**
- Set S showed the default path recovers no table structure on scans (TEDS
  0.26): Tesseract reads the words, but ADR-038's stitched rows hold line
  fragments, not columns, and side-by-side tables interleave (V5N1 p18, wer 0.30).
- The Tier 1 bake-off found Table Transformer (TATR) fixes those tables but, as a
  page-level switch, boxes two-column prose as tables and loses overall (+0.10
  wer); neither its confidence nor the box size separated the cases.
**Decision:** `parsers/tables.py`, an IR pass in `TesseractEngine.read_elements`,
run only on psm 3's reading (not when ADR-038's guard text wins).
- TATR detection (≥ 0.8) and structure recognition (v1.1-all), pinned revisions,
  Microsoft's reference pre-processing, a fixed number of CPU threads
  (`WISEAU_TABLE_THREADS`, default 1).
- Cells are filled with psm 3's own words: each word to the row and column it
  overlaps most, rows assigned per Tesseract line (skew), membership decided word
  by word (psm 3 reads a line straight across side-by-side tables, so such a line
  is split between them). The page's words are partitioned exactly.
- A TATR row stacking records (2+ cells with the same number ≥ 2 of printed
  lines) is split into one row per line, counted per column; a projected row
  header keeps its words in the first cell only if they run on without a column
  gap; TATR's spanning cells are not used.
- **The check refuses a region whose cells average more than 2.5 words**, and a
  region under 2 × 2. On the scored scans, prose boxed as a table averaged 3–67 words a cell; accepted tables 1.3–2.5.
- Chosen on the development sets only (`tatr_eval.py`, cached TATR runs), then
  run unchanged on the held-out sets and end to end.
- Opt-in, with PyTorch an optional dependency (`requirements-tables.txt`):
  without it, or with the variable unset, the pass is skipped, never an error.
**Consequences:**
- Set S table structure: TEDS 0.258 → 0.558 on the 8 documents all engines share
  (docling 0.513, GLM-OCR 0.717); S01 0.23 → 0.96, S07 0.23 → 0.88, held-out S04
  0.22 → 0.75. Block F1 0.718 → 0.756.
- No text changes: every changed page has exactly the default's words. Order
  moves with the cell reading: set D wer 0.072 → 0.052 (V5N1 p18 0.299 → 0.024),
  the other sets +0.001 to +0.007, held-out E unchanged, F +0.007.
- The check holds out of sample: on E and F, all 8 accepted regions are harmless
  (none costs > 0.1 wer) and all 7 damaging regions were refused.
- Cost: 7.1 → 9.0 s per scanned page on one thread (+26%; +8% on four), 2.8 s
  model load per process, peak memory 0.28 → 1.17 GB, +1.2 GB installed
  (PyTorch, transformers, timm, 265 MB of weights). Born-digital pages cost
  nothing. Deterministic across repeat runs and thread counts.
- Not covered: born-digital tables, tables TATR misses, list-like tables with
  long cells, spanning cells, headings, emphasis.
- `docs/benchmarks/2026-11-09-tables.md`.

## ADR-045 — The serializer writes tables with real cells as tables
**Date:** 2026-11-09 · **Status:** Accepted (roadmap 2.2, the explicit dialect; user asked for it)
**Context:**
- ADR-043's serializer reproduced the old output byte for byte. The IR now holds
  tables whose cells are real columns (ADR-046), and Markdown can say so.
**Decision:**
- A `table` element whose signal says its cells are columns (`CELL_SIGNALS`,
  today `tatr`) is written as a **pipe table** when its first row is all header
  cells and no cell spans others, else as an **HTML `<table>`** (`<th>` for
  headers, `colspan`/`rowspan`); `|` is escaped in pipe cells, `& < >` in HTML
  ones. HTML is valid Markdown and both scorers strip its tags.
- ADR-038's stitched tables stay plain rows of text (`STITCHED_AS_TABLES =
  False`): their "cells" are Tesseract line fragments, and written as tables
  they gain nothing (set S TEDS 0.279 → 0.288, typed F1 down) while claiming
  columns they don't have.
- Headings: OCR elements have none yet (roadmap 3.1); text-layer blocks keep
  PyMuPDF4LLM's own `#`. Plain OCR text is still not escaped.
**Consequences:**
- With the table pass off, every output is byte-identical to ADR-043/044's (15
  test PDFs whole, all scored pages): the dialect only applies where the IR has
  real cells.
- `docs/benchmarks/2026-11-09-tables.md`.

## ADR-044 — Text-layer blocks get boxes by aligning their words with the page's
**Date:** 2026-11-09 · **Status:** Accepted (roadmap Phase 14, item 2.1 follow-up; user asked for it)
**Context:**
- ADR-043's IR gave OCR elements boxes but left text-layer elements (PyMuPDF4LLM's
  Markdown blocks) without any: PyMuPDF4LLM's legacy Markdown reports no
  positions. Page furniture (3.2), paragraph continuation (3.3) and region routing
  (3.7) all need to know *where* a block sits, on born-digital pages too.
- PyMuPDF4LLM cannot be switched to its layout mode (ADR-012: non-deterministic),
  and re-deriving its blocks from `get_text("dict")` would change the Markdown.
**Decision:** `parsers/text_layer.py`, run on every text-layer page in
`file_parser._add_native_page` (`TEXT_LAYER_BOXES = False` turns it off).
- Both sides are cut into the same tokens (letters and digits, lower case; link
  targets and HTML tags such as `<u>` dropped), and the block tokens are aligned
  with the page's `get_text("words")` tokens by `difflib.SequenceMatcher`
  (`autojunk=False`). PyMuPDF4LLM sometimes emits blocks in another order than the
  page lists its words, so further passes align what is left (runs of 2+ tokens),
  until a pass aligns nothing (at most 32). A one-word block left over (a page
  number) is placed only if exactly one unclaimed page token can be it.
- Each element gets the union box of its aligned words, the page lines they sit
  on (with boxes) as `lines`, `bbox_signal = "text-layer-alignment"` and
  `bbox_coverage`, the measured share of its tokens that aligned. **Below 0.5 it
  gets no box** (refuse rather than guess, ADR-030); the coverage is still reported.
**Consequences:**
- V16 and V20 (the two born-digital volumes, 479 text-layer pages, 4,605
  elements): **99.3% of elements boxed** (4,573), 96.9% with coverage ≥ 0.9.
  Unboxed (32): footnote markers glued to words by the text layer (`Argentina2`
  against `Argentina <sup>2</sup>`), struck-through text, repeated labels whose
  words another block already claimed, and table fragments PyMuPDF4LLM split
  out of order (V20 p131, p173).
- Box quality: text blocks do not overlap on a real page, so a box bloated by a
  stray match would. Of 42,705 same-page pairs, 49 (0.11%) overlap by more than
  half the smaller box, on 9 pages: repeated caption text (V16 p52–53, "Rose –
  flower and plant parts" in every caption) and table rows PyMuPDF4LLM itself
  split into interleaved blocks (V20 p173, p224). Drawn on V16 p8 and V20 p173,
  the boxes sit on the right text.
- The Markdown is untouched (the serializer does not read boxes): V16 and V20
  converted whole are byte-identical to ADR-043's output, and so is every harness
  page re-run (sets A and S, 26 pages; the alignment only runs on text-layer
  pages, which in the harness are V16's). Time within noise (V20 whole: 26–28 s
  either way).
- JSON gains `bbox_signal` and `bbox_coverage` on such elements (schema stays
  `0.1`: additive). 555 tests pass, 8 skipped.

## ADR-043 — A document IR between the engines and the Markdown, serialized byte-neutrally
**Date:** 2026-11-09 · **Status:** Accepted (roadmap Phase 14, items 2.1–2.2; user asked for Tier 2)
**Context:**
- Every converter went straight from its engine's output to a Markdown string.
  What the engine knew about the text (each OCR line's box, which psm 3 groups
  ADR-038 stitched into a table, which page a block came from, which engine read
  it) was dropped at once, so no later stage could use it. Both external reviews
  (`docs/Document structure suggestion`, `docs/Wiseau OCR methodology review`)
  name this as the architectural gap: structure recovery (headings, furniture,
  continuations, captions) and region-level routing (roadmap 3.7, where Table
  Transformer is the lead) need elements with pages, boxes and provenance.
- The roadmap set the gate: the refactor must be provably behaviour-neutral
  before anything is built on it.
**Decision:**
- `parsers/document.py`: a `Document` is `pages` (number, size, `method`
  text-layer/ocr/remote, source engine), `elements` in reading order, and
  `relations`. An `Element` has `type`, `text` (as the engine produced it, never
  edited), `text_format` (plain/markdown), `source`, `page`, `bbox` (PDF points;
  `None` where the engine gives none), OCR `lines`, table `rows` of `Cell`s, a
  heading `level`, and the `signal`/`confidence` behind any inferred type
  (`confidence` is `None` unless calibrated: ADR-030's rule). `to_dict()` is
  deterministic (fixed field order, coordinates to 0.01). The vocabulary for Tier
  3 (caption, footnote, page_header, `caption_of`, `continues_from`, …) is
  defined but not yet produced.
- Adapters, the default path first: PyMuPDF4LLM's Markdown, split per page into
  typed top-level blocks (`serializer.split_markdown`, never inside a code fence;
  the fast path keeps one whole-document call with `page_chunks=True`, so heading
  sizes are still judged over all pages); the Tesseract path as psm 3 text blocks
  and row-stitched tables with every line's box, or ADR-035 paragraphs, or
  MuPDF's own OCR text; Mammoth, docling and GLM-OCR Markdown as unpaged blocks.
  `reading_order.order_paragraphs` and `tesseract_layout.groups` return the
  structure their string functions used to flatten; the string functions remain,
  as renders of it.
- `parsers/serializer.py` renders the IR with fixed joins: an element as its text
  (a table row by row), a page's elements by a blank line, the page stripped,
  blank pages skipped, pages by a blank line. `file_to_markdown` is
  `clean_markdown(to_markdown(file_to_document(...)))`.
- The IR is internal: no endpoint returns it, so there is no contract change (API
  stays 0.14.0). The harness writes it per page (`outputs/pymupdf/<page>.json`).
**Consequences:**
- **The gate held: byte-for-byte identical output.** All 15 test PDFs converted
  whole (859 pages: 382 OCR'd, 477 from a text layer; the fast path, the mixed
  path and the all-scanned path all exercised), a synthetic 8-page PDF
  interleaving text-layer, image-only and Acrobat-layer pages, and every scored
  benchmark page against its committed `outputs/pymupdf/` output. Whole-corpus
  time is unchanged (the 4-worker run took the same 1–5 s a page).
- One edge differs and is documented: a whitespace-only document now normalizes
  to `""` instead of `"\n"`. Every caller tests `markdown.strip()`, so both mean
  "no text" (the property test in `tests/test_document_ir.py` pins it).
- Tests that stubbed `_geometric_text`/`read_page` now stub
  `_geometric_elements`/`read_page_blocks`; the harness's `tesseract-unordered`
  engine patches `order_paragraphs`. 548 tests pass, 8 skipped.
- PyMuPDF4LLM's blocks have no boxes and their types are only as good as its
  Markdown (`signal="markdown-syntax"`); a box-bearing text-layer adapter (from
  `page.get_text("dict")`) is the follow-up, and must keep this gate.
- Next on top of it: the explicit dialect (pipe/HTML tables; changes output, so
  its own ADR), the structural gold set (2.3), then Tier 3 and TATR region routing
  (3.7) as IR passes. `docs/benchmarks/2026-11-09-ir-neutrality.md`.

## ADR-042 — The ADR-038 guard counts plausible words; Tesseract runs on one thread
**Date:** 2026-11-09 · **Status:** Accepted (hand-over 2026-11-08c item 0; the thread limit was found doing it)
**Context:**
- ADR-038 keeps MuPDF's OCR text as a guard: psm 3's text is used only if it has
  ≥ 85% of the guard's words. The count took every run of letters and digits, so
  speckle counted. After ADR-041 the guard read *more* junk than psm 3 out of P85
  p5's stamp box (243 runs against 192) and its worse text replaced psm 3's, losing
  "Address of Applicant: New Murphy Road and Concord Pike".
- While measuring that, psm 3 timed out (`TIMEOUT_S` = 120 s) on five set F pages
  that take about 10 s alone, because other jobs were running: the `tesseract` CLI
  is built with OpenMP and spreads each page over every core, and several such runs
  oversubscribe the CPU badly. On timeout the guard text is returned, so **the
  default output depended on the machine's load**. The service runs up to
  `MAX_CONCURRENT_JOBS` (4) conversions at once; the live test in the suite hit the
  same timeout under load.
**Decision:**
- `tesseract_layout.keeps_words` compares `plausible_word_count`s: a token counts
  if it has a digit, is a common short word, or has ≥ 3 letters, ≥ 2 different
  letters, a vowel and no capital after a small letter. `ocr.PLAUSIBLE_GUARD =
  False` restores the raw count.
- `read_page` runs the CLI with `OMP_THREAD_LIMIT=1`. On 12 pages across every test
  journal and patent the text is byte-identical to the multi-threaded run, and psm 3
  took 2–5 s a page with another OCR job running beside it.
**Consequences:**
- All 76 scored pages are byte-identical (the guard picks the same text on all of
  them), re-run with three other jobs on the box and no timeout.
- Across all 336 scanned pages of the 16 test PDFs (cached guard and psm 3 texts),
  the choice flips on 11. Eight get clearly better: junk dropped and real lines
  recovered (P85 p5, p8, p22; V4N1 p19; V5N1 p1, p17, p31; V5N3 p1, e.g. "Plant
  Varieties Journal", a printer's imprint). Three are pages of junk either way that
  get marginally worse (P85 p25, P97 p23's chemical drawing, P00 p41 loses a "FIG.
  3B" label). P85 against its own hidden layer (23 pages): wer 0.440 → 0.417,
  recall 0.851 → 0.858, precision 0.772 → 0.793.
- The rule was written looking at P85; the other 310 pages are the held-out check,
  and only 7 of them flip, so the evidence is mostly "no harm".
- A page whose psm 3 run genuinely needs more than 120 s still falls back to the
  guard; with one thread per run that now means a slow page, not a busy machine.
- `docs/benchmarks/2026-11-09-guard-and-threads.md`.

## ADR-041 — Scanner bands at a page's edges are painted out before OCR
**Date:** 2026-11-08 · **Status:** Accepted (roadmap 14 item 1.2, border cleanup; user asked for it 2026-11-08)
**Context:**
- The 1985 patent specification (P85) was scanned with a solid black band across
  the top and bottom of almost every page; some pages sit in a full black frame.
  After ADR-040 these pages are OCR'd, and Tesseract read the speckle in the bands
  as junk words: P85 p6 (set E) gained `t snes ee nsn bago ey set et ines …` and
  scored wer 0.110 against its own layer's 0.014.
- A survey of every scanned page in the 16 test PDFs found solid black regions of
  four kinds: P85's scanner bands; hairline strips along the very edge of some PBR
  scans; printed black header bars in the PBR journals; photos and banners inside
  the page.
**Decision:** `parsers/borders.py`, run first in `TesseractEngine.ocr_page`.
- A *band* is a connected region of solid cells (2 mm cells ≥ 70% near-black,
  on a 36 dpi render) that spans ≥ 50% of the page, lies wholly in the outer 18%
  margin, **runs off the page** at one end, and is **≥ 4% deep**. The last two
  were added after a first version also removed PBR edge hairlines and header
  bars: that changed no junk but nudged Tesseract's page segmentation, which lost
  a heading (V2 p23) and a caption (V1N3 p7) on PBR pages. With them, the rule
  fires on 25 of P85's 26 pages and on no page of any other test PDF.
- The band is covered with white **vector rectangles** drawn over a copy of the
  page, following its outline per cell column, bridging holes of ≤ 3 cells.
  Re-rasterising the page instead resampled every pixel (about 900k bytes changed
  on a page whose only edit was a 4 mm strip) and moved OCR results at random.
- Both OCR passes (the psm 3 layout and the ADR-038 guard) read the cleaned copy.
- `ocr.BORDER_CLEANUP = False` switches it off; the bake-off's `no-borders` engine
  does that and reproduces the previous default byte for byte on all 76 scored
  pages.
**Consequences:**
- Set E's P85 pages: p6 wer 0.110 → 0.053 (word precision 0.917 → 0.968), p21
  0.086 → 0.082. Set E overall 0.084 → 0.079. Every other scored page is
  byte-identical.
- P85's 23 other pages have no reference; scored against the PDF's own hidden
  layer as a silver reference (it scored wer 0.014 and 0.045 on the referenced
  pages): mean wer 0.585 → 0.440, precision 0.719 → 0.772, better on 17 pages.
  Recall falls 0.871 → 0.851. Part of that is text from the *neighbouring* page
  visible at the scan's edge (in the layer, not part of this page), but **p5 loses
  a real line** ("Address of Applicant: New Murphy Road …").
- The p5 loss is the ADR-038 guard, not the cleanup: the guard counts junk as
  words, so once the bands were gone it out-counted psm 3 on the stamp-covered
  cover page and replaced psm 3's correct text. The cleanup flips the guard's
  choice on 9 P85 pages, 7 of them toward psm 3. A guard that counts only
  plausible words is the follow-up; it changes every scan's path choice, so it
  needs its own held-out check.

## ADR-040 — A scan's invisible OCR layer is not trusted: such pages are OCR'd
**Date:** 2026-11-08 · **Status:** Accepted (roadmap 14 item 0.2: "if the layer loses, add a gate")
**Context:**
- Seven of the test PDFs are scans that carry an **invisible** text layer left by
  an earlier OCR engine: Vol 1 No 3 and Vol 2 No 2 (producer "Adobe Acrobat 8.13
  Paper Capture Plug-in") and the five patent specifications (no producer tag).
  Every one of their 225 pages has all its text in render mode 3 over a full-page
  image. The default path saw 16+ characters of text and read that layer as the
  page, never OCR-ing it.
- **Set L** (8 pages, a seeded *random* draw from the two Acrobat PBR journals,
  blind references, `docs/benchmarks/2026-11-08-layer-audit.md`): the old
  default scored wer 0.370, word recall 0.841, number recall 0.666. The layer had
  dropped whole tables (V2N2 p23 and p29 kept 4% of their numbers; V1N3 p19 lost
  the fee column; V2N2 p30 the table's figures), misread `1` as `l`, `Li` as `U`
  (`Lilium` → `ulium`), `St` as `5t`, joined words (`154lines`), and typed
  printed curly quotes as straight ones. PyMuPDF4LLM then read some layers'
  columns right-to-left (V2N2 p33, wer 0.972 with 98% of the words present).
  OCR-ing the same pages from their pixels with the ADR-038 path: **wer 0.042,
  word recall 0.977, number recall 0.960**, better than the old default on all 8
  pages.
- No signal that needs no reference picked out the pages where the layer wins.
  Coverage (layer words / OCR words < 0.85) catches only the two gross failures,
  and agreement between the two texts looked the same on pages either one won.
  Checking agreement costs an OCR pass anyway, so a gate saves no time either.
**Decision:**
- `file_parser._page_needs_ocr()` treats a page as a scan when *every* character on
  it is invisible (render mode 3) **and** one image covers ≥ 90% of the page. Such
  pages are OCR'd like image-only pages. GLM-OCR's per-page routing (ADR-036) uses
  the same test, so it now receives these pages too.
- A born-digital page (visible text), or one that hides a few characters behind
  visible text, is unaffected. Across all 16 test PDFs the rule fires on exactly
  the 225 layer pages and on no other page.
- `file_parser.OCR_INVISIBLE_LAYER` turns it off; the bake-off's `trust-layer`
  engine does that and reproduces the old default byte for byte.
- No API contract change, so no version bump. The output of these PDFs changes,
  the way ADR-035/037/038 changed the output of image-only ones.
**Consequences:**
- **Held out, set F's 8 Vol 2 No 2 pages** (drawn and frozen before this work):
  wer 0.057 → 0.045, number recall 0.885 → 0.947; no page worse by more than 0.001.
- **Do-no-harm, set E's 10 patent pages:** neutral on average (wer 0.094 → 0.092,
  word recall 0.942 → 0.949, number recall 0.677 → 0.732), but 5 of the 10 pages
  get worse. The patents' layer engine reads 1960s–80s typewriter faces better
  than Tesseract does (`e`/`o`/`c` confusions), and Tesseract reads the scanners'
  black edge bands as junk (P85 p6, 0.014 → 0.110). Both are recogniser and
  preprocessing problems for roadmap 1.2 and 1.3, not reasons to trust a layer we
  cannot check.
- These pages cost an OCR pass (about 5–7 s per page instead of under 1 s).
- `WISEAU_OCR_MODE=off` still reads only text layers, including invisible ones.

## ADR-039 — The normalizer keeps printed punctuation
**Date:** 2026-11-08 · **Status:** Accepted (user sign-off 2026-11-08; roadmap item 0.1)
**Context:**
- `clean_markdown()` rewrote ‘ ’ “ ” – — … to `' " - -- ...` on every
  conversion, so the output did not show what the source printed. Both external
  reviews (`docs/Document structure suggestion`, `docs/Wiseau OCR methodology
  review`) flagged it: the transcription should be the source text, unchanged.
- The born-digital PBR volumes carry real typographic punctuation. Measured on
  the default path (OCR off): Vol 20 No 4 has 1,409 such characters (‘ 663,
  ’ 721, – 13, “ ” 6 each) and Vol 16 No 3 has 2,863 (‘ 1,271, ’ 1,387, – 166,
  “ ” 19 each, … 1). All of it was flattened.
- The rewrite was not protecting any structure. No line in either volume begins
  with a dash or bullet, so the en-dash → hyphen row was not quietly creating
  Markdown lists.
**Decision:**
- Remove the quote, dash and ellipsis rows from `_CHAR_REPLACEMENTS`. Keep the
  no-break space → space, zero-width space and BOM rows: those characters are
  invisible, so rewriting them loses nothing a reader can see.
- Keep NFC and line-ending normalisation. Don't "smarten" straight quotes or
  hyphens either: the rule is *as printed*, in both directions.
- `keywords.py` folds ’ to ' in its matching key only, so "breeder’s" and
  "breeder's" in one document are one keyword. The term shown is still the most
  common spelling, as printed.
- Deferred until a consumer asks: a `simplified` (ASCII punctuation) output
  option, and preserving a trailing two-space hard break.
- API 0.13.0 → 0.14.0, because the default output changed for every consumer.
**Consequences:**
- Output is more faithful and stays deterministic and idempotent.
- A consumer that matched on ASCII `'` or `--` in the output now sees the
  printed characters. That is the contract change the version bump signals.
- `chapters.py` was checked and needs no change: its title matching strips all
  non-word characters, and its contents-page leader pattern already accepts – —.
  `naming.py` slugs on non-word characters, so filenames are unchanged.
- No OCR benchmark number moves. `score.py` drops punctuation, so punctuation
  fidelity is invisible to it; roadmap item 0.3 adds a punctuation-aware score.

## ADR-038 — Scans are read with Tesseract's own layout, table rows stitched back, ADR-035/037 as the guard
**Date:** 2026-11-06 · **Status:** Accepted
**Context:**
- Set C showed the default path's biggest remaining loss was ADR-035's geometric
  column detection on *mixed* pages: a table inside a column (V4 p23, wer 0.932),
  or a table above two-column prose (V4 p19, 0.563).
- Tesseract's own page segmentation (`--psm 3`) read those pages well and beat the
  default on 18 of 26 scans. It lost only on pages that must be read across each
  row (comparison tables, name/number lists), because it cuts each table column
  into its own block.
- The hand-over's candidate was "psm 3 by default, today's path for tables",
  which needed a page-level table signal. Number share (≥ 30%) misrouted pages in
  both directions.
- **Leaving ADR-012's MuPDF primitive** for these pages was the question ADR-035
  had deferred. psm 3's output is deterministic for fixed pixels, so the
  determinism argument behind ADR-012 does not apply against it.

**Decision:** fix psm 3's one failure inside its own output instead of routing
whole pages. `parsers/tesseract_layout.py`:
- Runs the `tesseract` CLI (psm 3, TSV) on the 300 dpi render.
- **Stitches** side-by-side blocks back into one table, read row by row, when both
  look like cells: median ≤ 4 words per line, ≤ 40% of the text width, ≤ 20% of
  the text width apart, overlapping vertically, and ≥ 60% of the smaller block's
  lines matching a same-height line at the same height. Prose columns have long
  lines, so they are never stitched. A stitched group sits where its first block
  was.
- Drops lines inside photo regions (ADR-037).
- `ocr.TesseractEngine` still computes the ADR-035/037 text and keeps it as the
  **guard**. If the layout text has fewer than 85% of the guard's words, or the CLI
  fails, the guard text is returned. On the dev pages psm 3 had discarded V4 p32's
  whole column of dates (54% of the words); every other page was ≥ 96%.
- `ocr.TESSERACT_LAYOUT = False` restores the ADR-037 path byte for byte
  (`tesseract-adr037` in the bake-off).

**Consequences:**
- **Designed on sets A–C** (26 scans): wer 0.149 → 0.048. Parameters were swept;
  the result is flat around the chosen values.
- **Frozen** in `9790218` behind the flag, then run on **held-out set D** (12 scans
  from two journals no earlier set had touched, references committed in `451a718`
  before any engine ran): wer 0.279 → 0.072, word recall 0.968 → 0.981, number
  recall 0.962 → 0.973. Better on 10 of 12 pages. The flag was switched on after.
- **All 38 scans:** 0.190 → 0.056; better on 30 pages, worse on 3.
- **Cost:** two OCR passes, ~8 s/page instead of ~4. Still far below GLM-OCR.
- **Known failures, for a set E:** short address lines in one text column stitched
  to a directory in the next (V5N3 p24, 0.044 → 0.133); two tables side by side
  read across both (V5N1 p18, 0.130 → 0.299; psm 3 alone does this too); V2 p12's
  typewriter table 0.148 → 0.172.
- The word-count guard catches *lost* text, not *misread* text: on a synthetic page
  that repeats identical words on every line, psm 3 removed the stacked letters as
  table rules and the guard could not tell. Real pages were not affected; the test
  now uses varied lines.
- Write-up: `docs/benchmarks/2026-11-06-set-d.md`.

## ADR-037 — Drop OCR text read out of photographs; render OCR rows by line centre
**Date:** 2026-11-03 · **Status:** Accepted
**Context:** Bake-off set B found two bugs in the default scanned-page path:
- **Photo noise.** Tesseract "reads" photographs as garbage. That was most of the
  path's loss on photo pages.
- **Row chaining.** ADR-035's `_rows` grouped lines by comparing each one with a
  row's *growing* extent, so one over-tall OCR box chained three printed lines
  into one row.

**Decision:**
- `parsers/photo_regions.py` renders the page at 36 dpi and splits it into cells
  about 2 mm square. A cell is *tone* when ≥ 60% of its pixels are mid-grey (grey
  level 50–200). A connected tone region of ≥ 150 cells is a photo.
- An OCR line ≥ 50% inside a photo is dropped unless it reads as real words
  (≥ 3 word-like tokens making up ≥ 80% of its characters), so banner text
  survives.
- Rendering groups a column's lines into rows by vertical centre (within half
  the median line height of the row's first line).
- Column *validation* keeps ADR-035's grouping. Changing it there regressed
  V4 p25 from wer 0.191 to 0.845, because the 0.7 density threshold was tuned on
  the old grouping.
- Both fixes sit behind module flags (`ocr.SKIP_PHOTO_REGIONS`,
  `reading_order.ROWS_BY_CENTRE`), so the bake-off can reproduce the ADR-035
  baseline byte-for-byte. Deterministic, no model, no measurable time cost.

**Consequences:**
- **Designed on sets A and B (16 scans):** wer 0.135 → 0.083.
- **Held-out set C** (10 scans, blind-transcribed before the fixes were
  written): wer 0.326 → 0.256, with no page worse. Two-column prose improved
  0.278 → 0.088; V2 p8 (photos) improved 0.598 → 0.066.
- **Limits:**
  - Photos on a near-black background are not detected.
  - Set C showed the larger remaining weakness is ADR-035's geometric column
    detection on *mixed* layouts: a table inside a column (V4 p23, wer 0.932), or
    a table above two-column text (V4 p19, 0.563). Tesseract's own layout
    analysis (psm 3) reads those at 0.016 and 0.194. See
    `docs/benchmarks/2026-11-03-set-c.md`.

## ADR-036 — GLM-OCR reads only the image-only pages of a PDF
**Date:** 2026-11-03 · **Status:** Accepted
**Context:** `engine="glm-ocr"` sent the whole PDF to GLM-OCR, born-digital pages
included. GLM-OCR reads only pixels, so it threw away an exact text layer. On the
bake-off's born-digital pages it scored wer 0.297 at 205 s/page, where PyMuPDF
scores 0.019 at 0.2 s. A per-page routing analysis on the committed outputs showed
"text layer → PyMuPDF, image-only → GLM-OCR" matches the best possible per-page
choice on every sample page except the V2 p17 typewriter table.
**Decision:** for a PDF, the GLM-OCR engine classifies pages with the same
text-layer test as the local OCR path (`_page_needs_ocr`, < 16 characters):
- **No image-only pages:** GLM-OCR is skipped and recorded as `no_image_pages`.
- **All pages image-only:** one request, as before.
- **Mixed:** text pages are read natively. Each consecutive run of image-only pages
  goes to GLM-OCR as its own PDF, and the parts are joined in page order.

A run GLM-OCR fails on falls back to local OCR for just those pages (ADR-014).
`WISEAU_OCR_MODE=force` sends every page. Images and DOCX are unchanged.
**Consequences:**
- Born-digital PDFs no longer pay GLM-OCR's minutes per page, and come out exact.
- **Metrics:** a mixed PDF makes one GLM-OCR call per image run, so `glm_ocr`
  success and fallback counts are now per call, not per conversion. `engines` still
  counts each conversion once: `glm-ocr` if any run converted, otherwise `ocr`.
- **Detection blind spots:** the text-layer test can be fooled by a scan with a
  scanner-added hidden OCR layer, or by broken font encodings. Those pages would be
  read natively.
- **Unchanged:** the service's page cap (`GLM_OCR_MAX_PAGES`) still applies, now
  per run. Not an API contract change, so no version bump.

## ADR-035 — OCR'd lines are put back in column order by geometry, not by a new engine
**Date:** 2026-11-03 · **Status:** Accepted
**Context:** On two-column scans the default Tesseract path found the right words
(word recall 0.96) but scored a word error of 0.66, because MuPDF's OCR primitive
groups recognised lines into blocks that run straight across the columns. The output
interleaved left and right line by line. Calling Tesseract directly (its own layout
mode) would have meant leaving the MuPDF primitive ADR-012 chose for determinism.
**Decision:** Keep the MuPDF primitive and re-order its lines in a new pure-geometry
module, `parsers/reading_order.py`, called by `TesseractEngine`:
- **Gutters:** find vertical strips that at most 5% of lines cross.
- **Validation:** a gutter is kept only if every column is at least 25% of the text
  width and its typical row is a continuous run of text (median row density ≥ 0.7).
  Table rows are mostly gaps between cells, so tables fail this and keep MuPDF's
  row-wise text.
- **Reading:** full-width lines cut the page into bands, and each band is read column
  by column.
- **No columns:** when no gutter qualifies, MuPDF's text is returned unchanged.

The module is deterministic, with no model and stable tie-breakers.
**Consequences:** On the bake-off scans, word error falls from 0.657 to 0.108, level
with GLM-OCR (0.103) at about 5 s/page instead of 106 (see
`docs/benchmarks/2026-11-03-tesseract-reading-order.md`). Born-digital pages and the
typewriter table (V2 p17) are byte-identical. Only the Tesseract engine uses it;
EasyOCR is unchanged. Not an API contract change: the response shape is the same and
only the text order improves, so the API version is not bumped.

## ADR-034 — GLM-OCR is a third opt-in engine, not a replacement for docling
**Date:** 2026-11-01 · **Status:** Accepted
**Context:** A proposal asked to replace docling with GLM-OCR. Its speed and memory
figures came from GPU serving and a model-only setup that skips the layout stage the
GLM-OCR report says prevents hallucination. GLM-OCR also reads only pixels, so it has
no DOCX reader and discards a digital PDF's text layer.
**Decision:** Add `glm-ocr` as an opt-in engine beside docling (`engine="glm-ocr"` or
`WISEAU_PDF_ENGINE=glm-ocr`), served by `glm-ocr/` (official SDK pipeline + the model
via transformers on CPU) and reached over `WISEAU_GLM_OCR_BASE`, with ADR-014's
fallback. DOCX never goes to it (`unsupported_type`). `/ping` gains
`available_engines` so a UI offers only engines the deployment runs. API 0.12.0 → 0.13.0.
**Consequences:** See `docs/benchmarks/2026-11-01-engine-bakeoff.md`. On CPU GLM-OCR
costs minutes per page and showed a runaway generation that dropped half a page, so
the service caps documents at 20 pages and the backend timeout is 300 s. A cloud VLM
(Gemini Flash) was the most faithful engine tested; adding one would break the
self-hosting/privacy stance and needs its own decision.
**Amended 2026-11-02:** the runaway was the SDK's `repetition_penalty=1.1`, not the
model or our serving: it loops in bf16 and float32 alike and never at 1.0, which is
also more faithful across the sample. `glm-ocr/service.py` now overrides it to 1.0
(`GLM_OCR_REPETITION_PENALTY`); see `docs/benchmarks/2026-11-02-glm-ocr-diagnosis.md`.

## ADR-033 — A batch's keywords are one request, one combined sidecar, and a table in each document
**Date:** 2026-08-27 · **Status:** Accepted
**Context:** ADR-032 deliberately left batch keyword extraction open, because the
question behind it had no obvious answer: a folder of twenty documents is twenty
calls against a `20/minute` limit — the exact problem ADR-031 solved for
conversion — but *what the answer looks like* was the part worth asking about.
The user settled it: **"if the user is batch processing stuff they should be able
to create keywords for every document being processed. The user should then have
the option to prepend the keywords to their respective markdown files and/or
download the metadata as a separate JSON."** That is the same two outputs ADR-032
gives a single document, applied per document — which leaves three real
decisions: how the batch is requested, how the sidecar is shaped, and what the UI
does with a panel that can only show one table at a time.
**Decision:** `POST /keywords/batch` (`extract_keywords_batch` on the MCP surface)
takes `{documents: [{markdown, source}], methods, top_k, language, prepend_table}`
and returns `{count, succeeded, failed, results[]}`, one entry per document **in
the order they were sent**. Each entry is a `KeywordResponse` plus `status`,
`error`, and the **`filename`** its keywords belong to — derived from `source` by
`parsers/naming.py`, the same rule `/convert/batch` uses, so passing back the
filenames a batch conversion returned makes the two sets of results line up
exactly. It is not a new kind of extraction: each document takes the path
`POST /keywords` would take it through, one at a time, **each taking its own job
slot**. `prepend_table` puts each document's *own* table into that document.
The **sidecar is one file, not one per document**: `keywords.json`, an array of
entries each keyed by the `.md` filename it describes. A twenty-document run
should be one click and one thing to open, and every entry already names the file
it belongs to. In the UI, the *Keywords* button reads **"Keywords (12)"** when a
batch is showing and extracts for all of them in one request; the panel then
shows the keywords of **whichever document is selected in the results list**, and
clicking another document switches the table with no further request. *Add to
Markdown* rewrites every document, so the existing **Download all (.zip)** and
each row's own *Save* carry their own table. API `0.11.0 → 0.12.0` (additive).
**Consequences:** The exclusion ADR-032 recorded ("no batch form") is now
**resolved**, and the shape matches ADR-031's so a client that already handles a
batch conversion handles this one: 200 whenever the *request* was valid,
per-document failures inside, and a failed entry with **no `filename`** — so
"write every entry that has a filename" remains the whole of a correct loop and
an empty keyword list can never be filed under a real document's name. A batch is
N extractions per token, so it gets its own bounds: `5/minute` against
`20/minute` for one document, `MAX_KEYWORD_BATCH_DOCS` (20), and
`MAX_KEYWORD_BATCH_CHARS` (8 000 000). That last one bounds the *work*, not the
memory — unlike an upload, a JSON body is already parsed by the time the route
runs, so there is no streaming guard to lean on; a deployment that cares should
bound the request body at its proxy. What this makes harder, knowingly: with
`keybert` selected a batch is tens of seconds *per document*, which will outlast
an HTTP gateway's idle timeout long before it outlasts `MAX_KEYWORD_BATCH_DOCS` —
the UI's progress estimate sums the per-document estimates so it at least reads
honestly, but bulk keyword work wants the fast default methods. `GET /metrics`
grows `keywords.batches` (requested, documents, failed, largest), because a
batch's cost is invisible in a request count. The **chapters** case is
deliberately unchanged: a chaptered document is one document split up, and
"keywords for this book" is what a reader wants there, not twelve per-chapter
rankings — so the button keeps acting on whatever the panel is showing.
Alternatives rejected: **a loop of single calls in the frontend** (one rate-limit
token per document, so a thirty-file folder gets keywords for the first half —
and it gives agents nothing, when "summarize this folder" is exactly the request
an agent makes); **one sidecar per document** (twenty downloads a browser would
block, or twenty entries in the archive that are harder to read than one list);
**putting the sidecars inside the `.zip`** (the archive is the *documents*, and
mixing metadata into it means a client unpacking Markdown has to filter);
and **a `keywords` flag on `/convert/batch`** (the ADR-032 argument, unchanged —
it would price extraction into every bulk conversion and force a re-convert to
change the method set).

## ADR-032 — Keyword extraction is a second call over converted Markdown, fused across methods by rank
**Date:** 2026-08-27 · **Status:** Accepted
**Context:** The ask: *"a keyword extraction methodology … as an extra option
**after** the markdown was created"*, using *"a mix of a few methods like YAKE,
spaCy and KeyBERT"*, with weights reported, downloadable as a JSON sidecar
and/or prependable to the document as a Markdown table. Two design questions
fell out of that. **Where does it hang?** The obvious move — a `keywords: true`
flag on the convert routes, mirroring `split_chapters` (ADR-030) — is wrong
here, and the word *after* is why. Chapter splitting has to be asked for up
front because the answer depends on the document arriving; keywords are
something a reader decides they want having *seen* the Markdown, and a flag
would mean re-converting to get them — up to a minute of docling on a scanned
report — every time someone changed their mind about which methods to run.
**How do four extractors become one list?** Their scores are mutually
incomparable: YAKE's is a *cost* on an unbounded scale (lower is better),
KeyBERT's is a cosine similarity in 0-1, spaCy's is an occurrence count, and the
built-in method's is a weighted frequency. Averaging them, or min-max
normalizing them against each other, invents a relationship that does not exist.
**Decision:** A separate endpoint, `POST /keywords` (`extract_keywords` on the
MCP surface), takes **the Markdown itself** plus `methods`, `top_k`, `language`
and `prepend_table`, and returns a ranked list in which every keyword carries
the rank and native score *each* method gave it, plus `agreement` — how many
methods found it. Rankings are combined by **Reciprocal Rank Fusion**
(`Σ 1/(60 + rank)`), which is scale-free, needs no tuning, and contributes
nothing for a method that did not run. The reported `score` is that sum divided
by the winner's, so the top term is `1.00` and the rest read as relative weights
rather than as an arbitrary small number. Four methods ship, chosen to be
*different in kind* rather than to be four of the same thing: **`frequency`**
(built into the engine, no dependency, and the only one that reads the
document's structure — a term in the title or a heading outweighs the same term
in a paragraph), **`yake`** (statistical; in the base requirements because it is
pure Python with no model download and carries stopword lists for many
languages), **`spacy`** (noun chunks and **named entities** — the terms a
frequency count under-ranks), and **`keybert`** (semantic relevance). The last
two are opt-in via `requirements-keywords.txt`, the bargain `ocr.py` already
strikes for EasyOCR (ADR-012). `prepend_table` returns the document with the
table inserted **under its own opening heading**, fenced in HTML comments so
re-running replaces the block instead of stacking one, and so extraction on an
annotated document analyses the document rather than its own output. API
`0.10.0 → 0.11.0` (additive).
**Consequences:** *The endpoint always answers.* `frequency` needs nothing, so a
lean deployment still extracts; a **named but uninstalled** method is reported in
`methods_skipped` and the others still rank (ADR-014's rule), while a method name
this build has never heard of is a **400**, the same split `resolve_engine`
makes. That means a caller must read `methods_used` rather than assume the
methods it asked for ran — which is also why `/ping` publishes `keyword_methods`
and `default_keyword_methods`, so the UI offers only choices that will work
(ADR-027's contract). *It says how sure it is, and refuses rather than guesses*
(ADR-030's rule): a document under 200 characters of text returns **no** keywords
with a `note` saying why, rather than promoting whichever noun occurred twice.
One curated list earns its place alongside the stopwords: a document's own
**section names** ("Executive summary", "Findings", "Recommendations") get no
heading prominence, because they appear in headings in every report whatever it
is about — without that, a report's top keywords are true of every report and
therefore say nothing about this one. They still rank on genuine repetition in
the body, so a document actually about its recommendations keeps them.
*Determinism holds* (invariant #1) — every method is deterministic for fixed
inputs, `keybert` included, on fixed weights and CPU inference with a pinned
seed; ties break on the term, never on dict order; and the JSON sidecar the UI
saves carries **no timestamp**, the same reason `zip.js` pins its dates.
Two implementation choices are worth not re-deriving. **KeyBERT runs as a
re-ranker over the candidates the other methods pooled**, with an explicit
`CountVectorizer`: its default one lowercases the document but not the supplied
vocabulary, defaults to unigrams, and strips English stopwords before forming
n-grams, so every capitalized term, every phrase, and every "X of Y" was being
dropped *silently* — a shorter list, never an error. And its **document embedding
is the mean of embeddings sampled across the whole document**, because a
sentence-transformer truncates at a few hundred tokens, so the obvious call
ranks every candidate against the report's cover page. Both were caught by
reading real output, not by a test. Bounds: `20/minute`, a job slot like any
heavy work (invariant #4), `MAX_KEYWORD_CHARS` (2 000 000) as a 413, and
`WISEAU_KEYWORD_MAX_CHARS` (400 000) as the analysis ceiling past which the
answer says it was truncated. `GET /metrics` grows a `keywords` block, because
which methods are *actually* running is invisible in a request count — a
deployment that installed the extras but never set `WISEAU_KEYWORD_METHODS` looks
busy while only ever running the cheap pair. Knowingly harder: sending the
document back up costs a round-trip of bandwidth that a convert-time flag would
not, and — as first shipped — there was **no batch keyword extraction**: a
folder of twenty documents was twenty calls against a `20/minute` limit, the same
problem ADR-031 solved for conversion. That gap is closed by **ADR-033**.
Alternatives rejected: **a flag on the convert routes** (re-converts to change
your mind, and prices keywords into every conversion); **normalizing the four
scores onto a common scale** (invents a relationship between a cosine and a
cost); **one method** (a single extractor's ranking has no confidence signal at
all — `agreement` is the whole point of running several); **YAML front matter**
instead of a table (asked for as a table, and a table is what a human reading the
Markdown can actually use); and **building the table in the frontend** (would
drift from what an agent asking the same question receives — the same reasoning
that put batch filenames in the backend, ADR-031).

## ADR-031 — Bulk conversion is one request with per-document results, and it excludes chapter splitting
**Date:** 2026-08-06 · **Status:** Accepted
**Context:** The ask: *"users may want to upload several documents at once and
download each Markdown conversion in a zip"*, with the explicit constraint that
**bulk processing and chapter splitting stay mutually exclusive for now**. The
frontend could have done all of this on its own — loop over the picked files,
call `/convert/file` once each, zip the answers with the `zip.js` written for
ADR-030 — and that is genuinely less code. It is also wrong in three ways. It
spends **one rate-limit token per document** against a `20/minute` route, so a
thirty-file folder is throttled halfway through and the user is left holding a
partial archive with no obvious way to finish it. It gives **agents nothing**:
the MCP surface and any HTTP caller would still have no way to say "convert
these", which is precisely the shape of request an agent asked to process a
folder makes. And it puts **filename policy in the browser**, where the chapter
filenames already coming from the backend would drift away from the batch's.
**Decision:** A new endpoint, `POST /convert/batch` (`convert_batch` on the MCP
surface), takes a repeated `files` part and returns
`{count, succeeded, failed, results[]}`, one entry per document **in the order
they were sent**. Each entry is a `MarkdownResponse` with three additions:
`status`, the `error` when it failed, and the `filename` to save it as. It is not
a new kind of conversion — each document takes exactly the path `/convert/file`
would take it through, one at a time, **each taking its own slot** in the
concurrency ceiling so a large batch queues fairly instead of holding the engine
for its whole run. Filenames come from the backend via a shared
`parsers/naming.py`, which `chapters.py` now uses too, so both features name
files by one rule. The **ZIP is still built in the browser** by ADR-030's
`zip.js`: the API's job is the contract, and a JSON response stays inspectable,
diffable and directly usable by an agent, which a binary archive is not. In the
UI, the chapters panel and the batch's document list are literally **the same
panel** — which is what mutual exclusivity ought to look like — and selecting a
second file disables the *Split into chapters* checkbox, **unticks it**, and says
why. On the API the same exclusivity is a **400**, never a silently dropped flag,
and `split_chapters` is simply absent from the batch MCP tool's signature, since
an agent picks a tool by its parameters. API `0.9.0 → 0.10.0` (additive).
**Consequences:** A batch is N conversions bought with one request, so it needs
bounds of its own or it *becomes* the way around the fair-use guards: hence
`5/minute` on the route (against `20/minute` for a single document),
`MAX_BATCH_FILES` (20) and `MAX_BATCH_BYTES` (50 MB), with documents read and
converted one at a time so peak memory stays one document rather than the whole
batch. Running out of the byte budget mid-batch is a request-level **413** —
nothing after that point could have converted either — while one outsized
document among ordinary ones is that document's own error. **Partial success is
the normal case, not an exception:** the response is 200 whenever the request was
valid, and an unreadable document carries the message the single-file endpoint
would have returned. A failed entry deliberately has **no filename**, so "write
every result that has a filename" is the whole of a correct save loop and an
empty file can never appear where a document should have been. What this makes
harder, knowingly: a batch with `engine=docling` is tens of seconds to minutes
*per document*, which will outlast an HTTP gateway's idle timeout long before it
outlasts `MAX_BATCH_FILES` — the UI's progress estimate sums the per-file
estimates so it at least reads honestly, but bulk work is really for the fast
default engine, and a batch of slow conversions may need a smaller
`MAX_BATCH_FILES` on a deployment with an aggressive proxy. `GET /metrics` grows
a `batches` block (requested, files, failed, largest), because a batch's cost is
invisible in a request count — thirty documents and one document are both a
single `POST /convert/batch`. The mutual exclusion is a *product* decision with a
real question behind it (what should an archive of twelve documents' chapters
look like — nested folders? flat with prefixes?); it is enforced in three places
so that lifting it later is a deliberate act, not something that half-works by
accident. Alternatives rejected: **client-side looping** (the rate-limit,
agent-surface and naming problems above); **a server-built ZIP** (forks the
response contract by content type, gives an agent bytes it must unpack to read,
and duplicates a ZIP writer that already exists in the frontend); and
**`/convert/files` as the path** (one letter from `/convert/file`, which is a
typo waiting to happen in a log, a metric label and a support conversation).

## ADR-030 — Chapters are found from the document's own contents page, and the split is opt-in
**Date:** 2026-08-06 · **Status:** Accepted
**Context:** A long PDF converts to one long Markdown string. The reader who
uploaded a 300-page book usually wants it *as chapters* — one file each — and
today has to cut it up by hand. Two questions had to be answered before writing
any code. First, **what marks a chapter?** Extraction flattens exactly the
signals a human uses: a chapter opening set in 18pt becomes `###`, or `**bold**`,
or (in a scan) a plain line indistinguishable from body text. Heading structure
alone is unreliable — it splits a report at its sub-sections and misses a
single-font novel entirely. But a document that *has* chapters nearly always
prints them in a **table of contents**, and that page survives extraction well:
"The Reckoning ........ 88" is a shape that regexes read easily. That is the
author's own list of chapters, which beats any structural guess. Second, **who
does the splitting?** Doing it in the browser would duplicate the heuristics in
JavaScript and give agents nothing; doing it in the backend serves the UI and the
MCP tools from one implementation, which is what ADR-003's single contract is
for.
**Decision:** A new deterministic module, `parsers/chapters.py`, splits
*converted* Markdown (post-`clean_markdown`, so it is engine-independent) using a
cascade: **contents page** → **heading structure** → **plain-text "Chapter N"
markers** → **nothing**. The contents page is only believed when at least half
its top-level entries are found again in the body **in document order** — the
gate that rejects a back-of-book index, whose entries never reappear forward.
Each chapter comes back with a title, a heading level, its Markdown, and a
zero-padded **filename**, so "save each chapter as a file" is a loop for the UI
and for an agent alike. The split is **opt-in** (`split_chapters`, default
false, on both convert endpoints and both MCP tools): it roughly doubles the
response and most documents have no chapters. `chapters`/`chapter_detection` are
`null` unless requested, so an existing client sees an unchanged response. API
`0.8.0 → 0.9.0` (additive). The browser builds the multi-chapter download as a
ZIP with a ~140-line `frontend/zip.js` rather than a library, keeping ADR-004's
no-build-step, no-third-party-script rule; entries are stored (uncompressed) with
a fixed 1980-01-01 timestamp so the archive is byte-identical run to run.
**Consequences:** Detection is a heuristic and will sometimes be wrong, so the
design makes that survivable rather than pretending otherwise: the chapters
**partition** the document (concatenating them reproduces it, so nothing can be
silently lost), `chapter_detection` reports *which* signal fired so a caller can
weigh it, the UI shows the list and lets a chapter be viewed before anything is
saved, and a result that looks like fragments (median chapter under 200
characters) is discarded in favour of `none` — refusing to split is a much
cheaper error than shredding a document. A failure in the splitter returns
`chapter_detection: "error"` with the document intact rather than a 502 — the
conversion is the thing the caller paid for, and a heuristic bug must not cost
it — while still logging a traceback and counting the method, so it cannot hide.
`GET /metrics` counts splits by method,
so an operator can see whether `toc` is actually carrying real documents or
everything is falling through to `none`. Known limits, written down rather than
discovered later: a contents page whose entries are worded quite differently from
the body headings loses coverage and falls back to headings; a document with
running page headers may match a chapter one page early; and only Latin-script
"Chapter/Part/Appendix" vocabulary is recognized for the marker path — the
contents-page path is language-agnostic, since it matches the document's own
titles. Alternatives rejected: an LLM/NLP model for boundary detection (a network
dependency and a stochastic default, against invariant #1 for a job that regexes
do well); PDF outline/bookmark metadata (would work only for PDFs that have it,
and not at all for the scanned government documents this project cares about,
which is also why the chapter code sees Markdown rather than the source file);
and a separate `POST /split` endpoint (a second surface for what is one flag on
an existing one).

## ADR-029 — The backend serves the MCP connector itself, on one container
**Date:** 2026-07-30 · **Status:** Accepted · **Amends:** ADR-028
**Context:** ADR-028 gave `mcp_server.py` an HTTP transport, which made a hosted
connector *possible* — but only as a **second process on a second port**, and
therefore a second deployment. On every free tier the user actually has (Cloud
Run, Render, Hugging Face Spaces) a service exposes exactly one port, so
"deploy wiseau so any LLM can use it" meant standing up and paying attention to
two services, wiring the second one's `WISEAU_API_BASE` at the first, and
keeping both awake. That is a lot of moving parts to hand someone new to
networking, and each one is a way for the deploy to fail. ADR-028's own
consequences also flagged two rough edges: the MCP endpoint had no request cap
of its own, and its Host-header allow-list defaulted to loopback, so a real
deployment answered `421 Misdirected Request` until its hostname was guessed
correctly and set by hand.
**Decision:** Serve the MCP endpoint **from the backend process**. `mcp_server`
exposes `hosted_routes()`; `main.py` grafts those routes onto the FastAPI app and
drives the transport's session manager from the app lifespan, so the deployed
container answers the REST API *and* `POST /mcp` on the same port and hostname.
Specifically:
- **Grafted, not `mount()`ed.** A mounted sub-app only matches `/mcp/…`, so the
  exact `/mcp` URL pasted into a connector would answer `307`. Grafting the
  transport's own `Route` makes the pasted URL the working one.
- **Stateless sessions.** A scale-to-zero host may answer two requests of one
  conversation from two instances; in-memory session state would not survive it.
- **The tools still call the API over HTTP**, now via loopback
  (`http://127.0.0.1:$PORT`, following the port the platform injects). This keeps
  invariant #4 — no in-process path around the rate limiter and the concurrency
  ceiling — at the cost of one local hop.
- **The endpoint is rate-limited like any other undecorated route.** slowapi
  identifies a route by `endpoint.__name__`, which an ASGI *object* does not
  have; without a shim the middleware raises before checking anything and every
  MCP request is a 500. `main._name_endpoint_for_limiter` names it.
- **The Host check is off unless asked for.** Loopback-only was correct for a
  local process and wrong for an endpoint whose purpose is to be reached at a
  public hostname. Unset (or `*`) ⇒ off, matching the API's existing
  `allow_origins=["*"]` stance; naming hosts in `WISEAU_MCP_ALLOWED_HOSTS`
  enforces them.
- **`WISEAU_MCP_PATH`** can move the endpoint to an unguessable path. A
  connector URL is the one thing every MCP client lets you set — custom auth
  headers are not (claude.ai's connector dialog has no field for one) — so this
  is the only access control that works everywhere. It is obscurity, not
  authentication, and the docs say so.
- `/ping` reports `mcp_endpoint`, so a client discovers the path instead of
  guessing it. API `0.7.0 → 0.8.0` (additive field, new surface).
- `WISEAU_MCP_MOUNT=0` switches it off; a checkout without the optional MCP SDK
  logs a warning and serves no endpoint rather than failing to start.
**Consequences:** One free container is now the whole product: a browser UI's
API, an OpenAPI schema for function-calling, and an MCP connector URL for any
LLM that can add one. `docs/hosting.md` is a click-by-click deploy guide built on
that. The standalone transports (`--transport stdio|streamable-http`) still work
and are still the right answer for a local agent; they are no longer the
deployment path. Two things to watch. (1) **The loopback hop shares one
rate-limit bucket**: every MCP conversion reaches `/convert/*` from `127.0.0.1`,
so all MCP callers together are held to that route's `20/minute`, not 20 each.
That is fine — desirable, even — for a personal deployment, and would need
rethinking before wiseau served many independent agents. (2) **The endpoint is
unauthenticated by default.** Anyone with the URL can convert documents on your
quota; the real cost guards are the platform's own (a max-instance ceiling, a
budget alert) plus the secret path. Real auth for a public deployment means
OAuth, which is a separate decision.

## ADR-028 — The MCP server gains a remote (streamable-http) transport, opt-in
**Date:** 2026-07-30 · **Status:** Accepted · **Amended by:** ADR-029
**Context:** `backend/mcp_server.py` only ran on the stdio transport — a client
has to spawn the Python process itself, so only agents on the *same machine* (or
one that can `ssh`/exec into it) could reach the tools. That fits Claude Desktop
and Claude Code fine, but nothing else: any LLM/agent surface that connects to a
*hosted* MCP connector over HTTP (claude.ai custom connectors, other agent
frameworks) had no URL to point at. The underlying `mcp` SDK already implements a
`streamable-http` transport; it was one `mcp.run()` argument away, unused.
**Decision:** `mcp_server.py` now takes a `--transport {stdio,streamable-http}`
flag (default from `$WISEAU_MCP_TRANSPORT`, then `stdio` — the existing local
wiring is unchanged unless you opt in). Choosing `streamable-http` binds
`$WISEAU_MCP_HOST`/`$WISEAU_MCP_PORT` (default `0.0.0.0:8080`, i.e. reachable
from outside the container/process) and widens the SDK's Host-header allow-list
with `$WISEAU_MCP_ALLOWED_HOSTS` (comma-separated hostnames) — the SDK's DNS
rebinding protection otherwise accepts only `localhost`/`127.0.0.1` requests, so
a remote deployment stays unreachable until its hostname is explicitly
allow-listed. No change to the tool surface (`convert_url`/`convert_file`/`ping`)
or to the underlying HTTP calls: this is still the same thin adapter over
`WISEAU_API_BASE`, now reachable by two transports instead of one.
**Consequences:** Any MCP client that speaks streamable-http — not just
subprocess-spawning local clients — can now use wiseau as a connector, which is
the point: any LLM, not just ones sharing a filesystem with the server. Running
the HTTP transport publicly means the process is a second network-facing
surface, on top of the FastAPI backend; it does not inherit the backend's
SlowAPI rate limiting (each tool call still does, since it's a normal HTTP
request to the backend, but the MCP endpoint itself has no request cap of its
own), so it should sit behind the same kind of network boundary (reverse proxy,
firewall, or the deployment platform's own access control) the operator would
put in front of any other public service — `WISEAU_MCP_ALLOWED_HOSTS` guards
against DNS rebinding, not against unauthenticated access. `docs/mcp.md`
documents both transports and how to wire each into a client.

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
