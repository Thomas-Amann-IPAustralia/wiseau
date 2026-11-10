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

- [x] **0.1 Stop rewriting punctuation in `cleaner.py`.** *Done 2026-11-08 (ADR-039, API 0.14.0), narrowly as recommended.* Keep curly quotes, en
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
  no OCR score. *User approved 2026-11-08.*
- [x] **0.2 Text-layer trust check (Vol 1 No 3).** *Done 2026-11-08 (ADR-040, `docs/benchmarks/2026-11-08-layer-audit.md`): on a random set L of Acrobat-OCR'd PBR pages the layer lost whole tables (old default wer 0.370, number recall 0.666; OCR 0.042 / 0.960). No reference-free gate separated the cases, so a scan whose only text is invisible is now always OCR'd. Held out on set F (0.057 → 0.045); neutral on set E's patents (0.094 → 0.092, 5 of 10 pages worse: typewriter faces and black scanner bands, for 1.2/1.3).* Blind-transcribe a few pages
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
- [x] **0.3 Harness metrics.** *Done 2026-11-08 (`docs/benchmarks/2026-11-08-tier0.md`): cer, line-level ord, pun_r/pun_p/pun_rf, rss, fail. Block order waits for 2.3's block annotations.* Add CER, a block reading-order score
  (normalised edit distance over block order), seconds/page, peak RSS and
  failure rate to `benchmarks/bakeoff/`. Add a punctuation-aware variant so 0.1
  is measurable. Report changes only, no new runs needed. *Effort: small.*
- [x] **0.4 Keep docling's JSON.** *Done 2026-11-08: `convert_document_full()` and the harness keep it. The pinned docling (2.113.0) has the heading-level stage but docling-serve 1.27.0 does not expose it; a pin bump would.* Have `docling_client.py` request
  `to_formats=md,json` and keep the JSON next to the Markdown in experiments, so
  the document tree and table spans aren't thrown away. Also check whether the
  pinned docling-serve version exposes the heading-level stage (bookmarks →
  numbering → typography). No contract change yet. *Effort: small.*

### Tier 1 — collect evidence before building more heuristics

- [x] **1.1 Set E from different publications.** *Drawn 2026-11-08 (`survey/set-e/`, seed 20261109, 2 pages per PDF, 12 pages); blind references frozen 2026-11-08 (`8247cc8`, `SAMPLE_E`). Default after ADR-040: wer 0.084.* The PDFs are supplied
  (2026-11-08, `pdf-test/Extra-4-Broad-Test/`): five Australian patent
  specifications from 1963 to 2000 (scans with hidden OCR layers, 145 pages) and
  one WO publication (`WO_2006070397_A1`, 21 image-only pages). These are typed
  and printed legal documents, not journals, so they test generalisation and
  hidden-layer handling. Same protocol as before: seeded draw, blind references,
  committed before any engine runs. Exclude near-empty pages and
  microcopy-resolution test charts (for example `1985043407` p1). *Effort:
  moderate (mostly transcription).*
- [x] **1.1b Held-out PBR pages for PBR-specific fixes.** *Done 2026-11-08: set F, 14 pages, blind references frozen in `1be3ded` (`SAMPLE_F`). No engine has run on it.* The gutter-stitch fix
  (V5N3 p24) and the side-by-side-table fix (V5N1 p18) are PBR failures, so they
  must be judged on PBR pages nobody has tuned against, with set E as the
  do-no-harm check. Vol 5 No 1 and No 3 have 46 unused image-only pages (15
  near-empty, and only one table-heavy page left). **Supplied 2026-11-08:**
  `Volume 4 Number 1.pdf` (36 pp, image-only) and `Volume 2 Number 2.pdf` (40
  pp, hidden OCR layer, about 8 table pages), both partly lower-resolution (150
  or 200 dpi). Draw from these; see `handover-2026-11-08.md`.
- [x] **1.2 Preprocessing ablation.** *Border cleanup done 2026-11-08 (ADR-041, `docs/benchmarks/2026-11-08-border-cleanup.md`): scanner bands painted out; P85 p6 wer 0.110 → 0.053, set E 0.084 → 0.079, no other scored page changed. Guard follow-up done 2026-11-09 (ADR-042). Deskew, normalisation, Sauvola and denoise done 2026-11-09 (`docs/benchmarks/2026-11-09-preprocessing-ablation.md`): all four lose against a pixel-exact control (wer +0.016, +0.041, +0.051, +0.003 on 72 scans); no per-page rule designed on the development sets holds on E and F, so the original pixels stay.* Run four variants of each page: the
  original, deskew + contrast/background normalisation, Sauvola thresholding,
  and light denoise. Score each through the current default, broken down by page
  type, and log skew, contrast, blur and noise per page. Keep the original as a
  candidate: only a per-page rule that beats it on held-out pages ships. OpenCV
  only. *Effort: low.*
- [x] **1.3 Specialist table and recogniser bake-off.** *Done 2026-11-09 (`docs/benchmarks/2026-11-09-recogniser-and-table-bakeoff.md`); nothing ships. PaddleOCR recognisers lose as line readers (spaces dropped in table rows) and gain only 0.004 re-reading Tesseract's word boxes, at 4× the time and up to 2.7 GB; no help on the 1963 typewriter faces. Table Transformer fixes V5N1 p18 (0.327 → 0.062) but reads two-column prose pages as tables (+0.101 overall); no gate tried separates them, so it moves to 3.7 with a prose-vs-table check. PP-StructureV3 is worse than the default on its own target pages (20 table pages: wer 0.332 vs 0.090), merges V5N1 p18's side-by-side tables, and needs 8–10.6 GB a page and PaddlePaddle 3.2.2.* Add Table Transformer
  (PubTables-1M) and PP-StructureV3's table module as harness engines. Run them
  on the known failure pages: V5N1 p18 side-by-side tables, V5N3 p24 directory,
  V16 p90, V2 p17 typewriter table, narrow-column, merged-cell and
  short-first-column tables. Add PP-OCRv6 (small model, CPU) as a recogniser
  next to Tesseract. Record memory, since PaddlePaddle must fit a 16 GB CPU
  Space. *Effort: moderate.*

### Tier 2 — the architectural change

- [ ] **2.1 Canonical document IR.** *Core done 2026-11-09 (ADR-043): `parsers/document.py`; adapters for the PyMuPDF4LLM text layer (typed Markdown blocks, no boxes yet), the Tesseract path (psm 3 blocks and stitched tables, ADR-035 paragraphs, every OCR line's box), and Mammoth/docling/GLM-OCR Markdown as unpaged blocks. Text-layer blocks got boxes 2026-11-09 (ADR-044: word alignment, 99.3% of V16/V20 blocks boxed, refusal below 0.5 coverage). Still open: docling's JSON tree as an adapter, and the URL path.* A JSON tree with elements (title,
  heading+level, paragraph, list/item, table with cell spans, figure, caption,
  footnote, equation, header/footer, page number) and relationships (parent_of,
  caption_of, footnote_for, continues_from, continues_table, references,
  source_for). Every element carries page, bounding box, the source engine, and
  the original text, which is never edited. Recovered structure goes in separate
  fields with a `confidence`, so nothing overwrites the transcription. Adapter
  order: PyMuPDF text layer and the Tesseract layout path first (the defaults),
  then docling JSON, Mammoth, and the URL path. *Effort: large.*
- [ ] **2.2 Deterministic Markdown serializer.** *Gate passed 2026-11-09 (ADR-043, `docs/benchmarks/2026-11-09-ir-neutrality.md`): `parsers/serializer.py` replaces the string path, byte for byte on all 15 test PDFs whole (859 pages), a mixed-path PDF and every scored page. Dialect done 2026-11-09 (ADR-045): tables with real cells are written as pipe or HTML tables; ADR-038's stitched rows stay plain (no gain, false columns).* IR → Markdown. **Gate: its
  output must match today's Markdown byte for byte before it replaces the current
  path**, so the refactor is provably behaviour-neutral. After that, adopt an
  explicit dialect: CommonMark plus selective HTML. Simple tables use pipe
  syntax; tables with merged cells or nested headers use HTML. That costs no
  WER, because the scorer strips HTML tags. *Effort: moderate.*
- [x] **2.3 Structural gold set and metrics.** *Done 2026-11-09 (`docs/benchmarks/2026-11-09-structure-gold-set.md`): set S, 12 documents / 20 pages / 399 blocks / 43 relations, blind from page images, frozen in `fb08ab2`; `structure.py` scores block F1, types, split/merge, order, headings (F1, level, tree edit distance), TEDS, emphasis and relations. Gaps: no deeply nested report, no table repeating its header across pages. First scores: the default path recovers no headings, tables or emphasis on scans; docling and GLM-OCR find headings but level all of them `##`; GLM-OCR leads on tables (TEDS 0.72).* Annotate 10–15 documents for
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
- [~] **3.7 Region-level routing.** *Tables done 2026-11-09 as an opt-in pass (ADR-046, `WISEAU_TABLES=tatr`, `docs/benchmarks/2026-11-09-tables.md`): TATR regions read into cells from psm 3's words; a region averaging > 2.5 words a cell is refused as prose (held out: all 7 damaging regions refused, no accepted one damaging). Set S TEDS 0.258 → 0.558; no word changes; +26% time per scanned page on one thread, +0.9 GB peak memory. On in the Docker image since ADR-047 (user accepted). Open: re-OCR of suspicious regions; born-digital tables.* Once a layout detector feeds IR regions,
  send prose to Tesseract and tables to the table pipeline, and re-OCR only
  regions that look suspicious (low calibrated confidence, missing rows,
  uneven column lengths). This replaces page-level routing, which failed its
  held-out test on set C.
- [x] **3.7b Layout-region bake-off for scans (spike, no production change).** *CPU leg done 2026-11-10 (`docs/benchmarks/2026-11-10-layout-regions.md`): every word kept; PP-DocLayoutV3 regions regrouped over `tables-tatr` lift set S blk_f 0.649 → 0.766, merge 0.332 → 0.138, hd_r 0.10 → 0.75, htree 0.08 → 0.50, TEDS unchanged, ord 0.029 → 0.041; regresses S06 (stitched list read by column; directory split per entry). OvisOCR3 is level with the detector (boxes nearly identical, same label set) at ~180× the time (150 s vs 0.82 s a page, 5.2 vs 1.6 GB): dropped, with TeleOCR. Kaggle T4 leg prepared (`benchmarks/bakeoff/kaggle/`), optional now. Next: a detector-regroup candidate behind its own ADR, fresh held-out structure pages, the S06 and `vision_footnote` fixes, the serializer gate.*
  *Proposed 2026-11-09 (`docs/assessment-2026-11-09-ovisocr3-teleocr.md`, from
  `Alt-solution-investigation.md`).* 3.7 has no general layout detector yet
  (headings, captions, page furniture, figures). Compare OvisOCR3's layout-only
  mode (GPU on Kaggle for quality; 4-vCPU timing for cost) against a CPU layout
  detector (DocLayout-YOLO or similar) as harness engines. Each engine's regions
  are filled with Tesseract's psm 3 words, serialized and scored with
  `structure.py`. Develop on sets A/B, then score set S once with everything
  frozen; word metrics must not move. Probe V16 p90 / V5N1 p18 first (PP-DocLayout
  merged them). Integration only after a set S gain and an ADR. *Effort:
  moderate; the Kaggle leg needs the user's account.*

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
