# Hand-over: Phase 14 Tier 0 done, set F frozen, set E drawn (2026-11-08, second session)

Read this after `CLAUDE.md` and `STATUS.md`. The plan is still
[`roadmap-ocr-and-structure.md`](roadmap-ocr-and-structure.md). The benchmark
procedure, environment rebuild steps and pitfalls in
[`handover-2026-11-08.md`](handover-2026-11-08.md),
[`handover-2026-11-06.md`](handover-2026-11-06.md) and
[`handover-2026-11-03.md`](handover-2026-11-03.md) still apply.
Branch: `claude/tender-goodall-hdjjuv`.

**Standing user instruction: no paid API tokens** (Gemini, OpenAI) until the user
says otherwise. Nothing in this session used one.

## What happened

1. **Item 0.1 shipped (the user approved it).** ADR-039: `clean_markdown()` keeps
   ‘ ’ “ ” – — … as printed. API 0.13.0 → 0.14.0. `keywords.py` folds ’ → ' in its
   matching key only. Re-running the default path on sets A–D changed 41 of 42
   outputs *only* by the old mapping (one identical).
2. **Item 0.3 shipped.** `score.py`: `cer`, `ord` (line reading-order error),
   `pun_r` / `pun_p` / `pun_rf`, and `rss` / `fail` from `runs.json`.
   `run_engines.py` writes `runs.json` and carries on past a failing page. Every
   earlier metric is unchanged on all 530 page scores. Details:
   [`benchmarks/2026-11-08-tier0.md`](benchmarks/2026-11-08-tier0.md).
3. **Item 0.4 shipped.** `docling_client.convert_document_full()` returns docling's
   JSON tree with the Markdown; the harness's docling engine writes `<page>.json`.
   No contract change. The pinned docling (2.113.0) has a heading-level stage,
   but docling-serve 1.27.0 cannot switch it on; a pin bump would.
4. **Set F (held-out PBR) is drawn, transcribed blind and frozen** (`1be3ded`).
   14 pages from Vol 4 No 1 and Vol 2 No 2, seed 20261108, weighted to tables
   (5 table pages), 7 at 150–200 dpi and 7 at about 300 dpi. `SAMPLE_F` /
   `GROUPS_F` in `regions.py`; `--set f` in `run_engines.py` and `score.py`.
   **No engine has run on set F.**
5. **Set E (do-no-harm) is drawn but not transcribed** (`71cb0aa`). 12 pages, two
   from each of the six PDFs in `pdf-test/Extra-4-Broad-Test/`, seed 20261109,
   `--per-doc 2`, near-empty pages and the P85 p1 test chart excluded. The draw is
   in `benchmarks/bakeoff/survey/set-e/draw.json`.

Tests: 513 passed, 8 skipped.

## Findings to carry forward

- **Scanned references from sets A–D record quotes as typed, not as printed.**
  They hold 1,008 `'` against 189 ‘ ’, while the print is often curly (checked on
  Vol 5 No 1 p11). So `pun_*` on those sets measures typing convention; use
  `pun_rf` there. Sets E and F are transcribed with printed punctuation.
- **Tesseract reads printed en dashes as hyphens** (191 in references, none in
  output). A recognition loss, not a cleaner one. A candidate for the recogniser
  bake-off (1.3).
- **Vol 2 No 2's hidden OCR layer drops text on some pages.** By the survey's word
  counts, the layer holds 27 of 261 words on p25, 35 of 245 on p27, 251 of 585 on
  p23 and 329 of 505 on p21. The default path trusts the layer on all 40 pages, so
  that text never reaches the output. This is direct evidence for item 0.2.
  (None of those four pages is in set F; p3, p4, p6, p10, p12, p16, p31 and p32
  are, and their layers hold 92–100% of the OCR word count (p10 is the lowest,
  316 of 342), so set F mainly tests the layer's
  *accuracy*. Its *coverage* failures need the dev pages above.)
- Set E's patent layers have no producer tag, and the survey found all five
  patent PDFs have a layer on every page.

## What to do next, in order

1. **Rebuild the environment**: `apt-get install -y tesseract-ocr`, then
   `pip install -r backend/requirements.txt -r backend/requirements-dev.txt`;
   confirm 513 passed, 8 skipped.
2. **Transcribe set E blind**, before any engine runs on it. Same conventions as
   set F (below). Check the drawn patent pages for duplicates first: a patent PDF
   can repeat its abstract or front page. Add `SAMPLE_E` / `GROUPS_E` (group by
   document) to `regions.py` and `--set e` to the runner and scorer, then commit
   the references.
3. **Item 0.2, the text-layer trust check.** Add a `hidden-layer` engine to
   `run_engines.py` (`page.get_text()` on the single-page PDF, no OCR), and run it
   with `pymupdf` (which today returns that same layer for these pages) and a
   forced-OCR variant (render the image, OCR with the ADR-038 path) on set F's
   eight V2N2 pages and set E's ten patent pages. Design any gate on development
   pages (V2N2 p21, p23, p25, p27; Vol 1 No 3), then hold it out on sets E and F.
4. Then run the current default on set F as the held-out baseline for Tier 1
   (gutter stitch, side-by-side tables, preprocessing ablation 1.2, recogniser and
   table bake-off 1.3).

## Transcription conventions used for set F (keep them for set E)

- Read column by column; tables row by row, a multi-line cell line by line
  across; the page number last.
- Keep printed typos and defects ("Nautilus’" with no opening quote, "3.6 %%").
- **Printed punctuation as printed**: ‘ ’ “ ” for typeset quotes, – in ranges where
  the print has an en dash, — for dashes and empty table cells, `-` where the print
  has a hyphen (Vol 4 No 1's condensed tables print hyphens in ranges), ° for the
  degree sign.
- Skip text printed inside photographs (V4N1 p22's labels). Labels on white beside
  a figure are kept (V2N2 p31's gel-lane names). That was a judgement call.
- Crops: render each region at 200–290 dpi with a crop helper (the one used this
  session took `KEY PAGE x0 y0 x1 y1 DPI`, fractions of the page); 150 dpi pages
  gain nothing past about 2× but are easier to read.
