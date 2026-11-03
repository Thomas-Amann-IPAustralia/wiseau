# Column-aware reading order for Tesseract: 2026-11-03

**Change:** ADR-035. `backend/parsers/reading_order.py` re-orders the lines that
MuPDF's OCR returns, so a two-column scan is read column by column instead of line
by line across the page.

**Result:** on the six scanned bake-off pages, the default Tesseract path's word
error falls from **0.657 to 0.108**, level with GLM-OCR (0.103), at about 5 s/page
instead of 106. No paid APIs were used.

Reproduce from the repo root (needs the `tesseract` binary):

```bash
python benchmarks/bakeoff/run_engines.py pymupdf
python benchmarks/bakeoff/vote.py
python benchmarks/bakeoff/score.py
```

## The cause

MuPDF's OCR (`Page.get_textpage_ocr`) recognises each printed line correctly and
gives it an exact bounding box. But it groups lines into blocks that run straight
across the page, so on V2 p6 the plain text read:

```
PART 2 __ MATTERS FOR                          ← left column (x 72–283)
a medium large diameter double flower; with    ← right column (x 312–500)
many petals, some arranged in a loose central  ← right column
PUBLIC NOTICE                                  ← left column
```

## The fix

Pure geometry over those line boxes; details in ADR-035 and the module docstring.

1. **Find gutters:** vertical strips that at most 5% of lines cross.
2. **Keep only gutters between real text columns:** every column at least 25% of
   the text width, and its typical row a continuous run of text.
   - The row-density test is what keeps tables out. On these pages, text columns
     measure 0.8–1.0. Every split that would cut the V2 p17 typewriter table has a
     column at 0.6 or below, because table rows are mostly gaps between cells.
   - The first attempt used the median *fragment* width instead. It rejected four
     of the five two-column pages, because MuPDF splits OCR lines into many short
     fragments.
3. **Read in bands:** full-width lines cut the page into bands, and each band is
   read column by column.
4. **Fall back:** no qualifying gutter means MuPDF's text is returned unchanged.

## A scorer bug, found along the way

`score.py` stripped HTML tags with `<[^>]+>`, which matches *across lines*.
Tesseract's V4 p11 output contains stray OCR specks such as `<` and `=>`. The scorer
deleted about 110 real words between them.

- It now matches only single-line tags (`</?[A-Za-z][^<>\n]*>`).
- That changes three committed scores:
  - Tesseract V4 p11: word recall 0.736 → 0.918, wer 0.905 → 0.960.
  - docling V4 p7 and V4 p11: wer up by about 0.003.
- All figures below use the fixed scorer, the "before" row included, so the
  comparison is fair. Earlier write-ups quote the old scorer.

## Results (fixed scorer, same references and pages)

| Engine | Pages | wer ↓ | bow_r | bow_p | num_r | num_p | s/page |
|---|---|---|---|---|---|---|---|
| Tesseract, before (interleaved) — scanned | 6 | 0.657 | 0.960 | 0.944 | 0.855 | 0.903 | 5 |
| **Tesseract + reading order — scanned** | 6 | **0.108** | **0.961** | 0.944 | 0.858 | 0.904 | **5** |
| docling — scanned | 6 | 0.214 | 0.841 | 0.926 | 0.684 | 0.912 | 41 |
| GLM-OCR — scanned | 6 | 0.103 | 0.930 | 0.956 | 0.895 | 0.892 | 106 |
| Gemini 3.5 Flash (paid API) — scanned | 6 | 0.034 | 0.996 | 0.999 | 0.993 | 0.994 | 22 |
| PyMuPDF — digital (unchanged) | 4 | 0.019 | 0.995 | 0.993 | 1.000 | 1.000 | 0.2 |

Per scanned page (wer / bow_r / num_r):

| Page | Tesseract before | Tesseract after | GLM-OCR |
|---|---|---|---|
| V2 p6 | 0.733 / 0.986 / 0.953 | 0.072 / 0.986 / 0.953 | 0.016 / 0.997 / 0.953 |
| V2 p17 (typewriter table) | 0.036 / 0.982 / 0.963 | 0.036 / 0.982 / 0.963 (byte-identical) | 0.462 / 0.621 / 0.607 |
| V2 p20 | 0.658 / 0.989 / 0.964 | 0.046 / 0.989 / 0.964 | 0.032 / 0.994 / 0.982 |
| V4 p7 | 0.794 / 0.894 / 0.653 | 0.188 / 0.895 / 0.673 | 0.029 / 0.995 / 0.980 |
| V4 p11 | 0.960 / 0.918 / 0.615 | 0.267 / 0.918 / 0.615 | 0.027 / 0.973 / 0.865 |
| V4 p26 | 0.761 / 0.993 / 0.980 | 0.036 / 0.993 / 0.980 | 0.054 / 0.996 / 0.980 |

- **Word content is unchanged, by design.** Word recall and precision move only by
  rounding; the gain is all reading order.
- **The born-digital pages and V2 p17 are byte-identical** to before.
- **Where Tesseract still trails GLM-OCR: V4 p7 and V4 p11.** It misreads numbers
  there (number recall 0.67 and 0.62 against 0.98 and 0.87). That is recognition,
  not order, so this change can't reach it.
- **Same average, different strengths.** Tesseract now finds more words than GLM-OCR
  (0.961 against 0.930) but fewer numbers (0.858 against 0.895).

## The voting experiment, re-run

[The voting experiment](2026-11-03-voting-experiment.md) was built on the
interleaved Tesseract output. Re-run on the reordered output, its conclusion
changes:

| Method (scanned) | wer | bow_r | num_r | s/page |
|---|---|---|---|---|
| GLM-OCR alone | 0.103 | 0.930 | 0.895 | 106 |
| Tesseract + reading order alone | 0.108 | 0.961 | 0.858 | 5 |
| **vote, Tesseract as tie-breaker** | **0.061** | 0.951 | 0.839 | 152 |
| vote, GLM-OCR as tie-breaker | 0.088 | 0.929 | 0.884 | 152 |
| consensus-words router | 0.096 | 0.963 | 0.861 | 152 |
| oracle best single (cheats) | 0.028 | 0.988 | 0.947 | — |

- **Voting now beats every single offline engine on word order.** It needed
  Tesseract's lines in the right order to align with the others.
- **It still costs all three engines' time, about 150 s/page.**
- **It still loses numbers** (0.839 against GLM-OCR's 0.895).

So voting is an option for a slow, opt-in "best offline" mode. It is not a default.

## Caveats

- Six scanned pages from two journals, all with the same two-column layout. Other
  layouts are covered only by unit tests (three columns, full-width headings,
  tables, side margins).
- Thresholds were checked on these same pages. That risks overfitting, so check
  them on new scanned journals before relying on them.
- The scanned references were drafted from Tesseract (per region) and then
  hand-corrected. That may flatter Tesseract's wording, though not its column
  order, which the old output got wrong.
- Mixed layouts are not handled well: a table spanning both columns above
  two-column text would be read one column at a time. None of the sample scans has
  one.
