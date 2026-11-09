# Hand-over: sets E and L transcribed, text-layer audit done, ADR-040 shipped (2026-11-08, third session)

Read this after `CLAUDE.md` and `STATUS.md`. The plan is still
[`roadmap-ocr-and-structure.md`](roadmap-ocr-and-structure.md). Earlier hand-overs
([`b`](handover-2026-11-08b.md), [`2026-11-08`](handover-2026-11-08.md),
[`2026-11-06`](handover-2026-11-06.md)) hold the benchmark procedure, environment
rebuild and pitfalls. Branch: `ccr-ecf86318-0znnw0`.

**Standing user instruction: no paid API tokens** (Gemini, OpenAI) until the user
says otherwise. Nothing in this session used one.

**The user's stance (2026-11-08):** "The more I learn about OCR the less I trust
anyone/anything besides our process." They asked for a random sample of pages
already OCR'd by Adobe to be checked for errors. Read that as: don't pass through
someone else's OCR unchecked.

## What happened

1. **Set E transcribed blind** (12 pages; `SAMPLE_E`, grouped by publication).
   Typed patent margin line numbers kept; handwritten margin numbers and chemical
   structure drawings left out. No duplicate scans among the drawn pages.
2. **Set L drawn and transcribed blind** (`c012815`, `8247cc8`): a plain random
   sample (seed 20261110) of 4 pages each from Vol 1 No 3 and Vol 2 No 2, the two
   journals with an Acrobat Paper Capture layer, set F excluded.
3. **Item 0.2 / ADR-040.** The layer lost whole tables (V2N2 p23, p29), the fee
   column of V1N3 p19 and table figures on V2N2 p30, and misread `1`/`l`,
   `Li`/`U` and so on. Old default on set L: wer 0.370, number recall 0.666; OCR
   of the pixels: 0.042 / 0.960. No reference-free gate worked, so
   `_page_needs_ocr()` now treats "all text invisible + one image covering ≥ 90%
   of the page" as a scan. Fires on exactly the 225 layer pages of the 16 test
   PDFs. Held out on set F's V2N2 pages: 0.057 → 0.045. Patents (set E): neutral,
   5 of 10 pages worse.
4. Harness: `hidden-layer`, `forced-ocr`, `trust-layer` engines; `word_errors.py`
   (lists misread/lost/inserted words); `--set e`, `--set l`.

Tests: 519 passed, 8 skipped.

## Findings to carry forward

- **Patent layers are good; our recogniser is the weak link there.** On the
  1963/1985 typewriter faces Tesseract confuses e/o/c/a, and it reads the black
  scanner bands at the page edges of the P85 scans as junk words (P85 p6 0.014 →
  0.110). Border cleanup belongs in the preprocessing ablation (1.2), typewriter
  faces in the recogniser bake-off (1.3). Sets E and L now give both a baseline.
- **PyMuPDF4LLM read some Acrobat layers right column first** (V2N2 p33). That
  path no longer runs on those pages, but it is a reminder that PyMuPDF4LLM's
  order is untested on anything but born-digital PBR volumes.
- **Held-out PBR baseline for Tier 1 (set F, all 14 pages, new default):** wer
  0.046, cer 0.014, ord 0.075, word recall 0.966, number recall 0.947.
- No adjudication pass has been run on sets E, F or L yet.

## Later the same session: border cleanup (ADR-041)

The user said "keep ADR-040 on, start the border cleanup next". Done:
`parsers/borders.py` paints out solid scanner bands (only the 1985 patent has
them). P85 p6 0.110 → 0.053; nothing else scored changed. Read
`docs/benchmarks/2026-11-08-border-cleanup.md` before touching it: two plausible
first versions were wrong (re-rasterising resamples the page; removing hairline
edge strips perturbs Tesseract's segmentation). `silver_layer.py` scores an OCR
flag against a PDF's own hidden layer on pages without references.

## What to do next, in order

0. **Fix the ADR-038 guard's word count** (count plausible words, not junk):
   it cost P85 p5 a real line once its bands were gone. It can change the path of
   any scan, so re-run the default on every scored set and check P85 with
   `silver_layer.py`.

1. Optional, cheap: adjudicate sets E/F/L references against the disagreements
   between `forced-ocr` and `hidden-layer` (`disagreements.py` pattern), against
   the page images.
2. Tier 1 per the roadmap: preprocessing ablation 1.2 (include border/black-band
   removal; judge on set E's P85 pages and set F), then the recogniser and table
   bake-off 1.3 (include typewriter pages P63a/P63b/P85), then the gutter stitch
   and side-by-side table fixes, held out on set F.
