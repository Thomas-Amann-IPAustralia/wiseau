# Recogniser and table bake-off: PaddleOCR, Table Transformer, PP-StructureV3 (2026-11-09)

Roadmap 14 item 1.3. No paid APIs. No behaviour change: nothing here ships. Code:
`benchmarks/bakeoff/ml_engines.py`; engines `rec-*`, `wrec-*`, `tatr`,
`ppstructure-v3` in `run_engines.py`.

## Method: change one stage, hold the rest fixed

The default path's psm 3 stage (ADR-038) gives every line and word a box. So:

- **`rec-tesseract`** is the control: the psm 3 layout, rows stitched, photo mask
  (ADR-037), Tesseract's own text. It skips the ADR-038 guard and the band cleanup
  (ADR-041), so it is a little worse than the default (`pymupdf`) on a few pages
  (all 72 scans: wer 0.067 against the default's 0.056). Compare every engine
  below with it, not with the default.
- **`rec-MODEL`** re-reads every line box with a PaddleOCR recogniser.
- **`wrec-MODEL`** re-reads every *word* box and keeps Tesseract's spaces. Added
  after `rec-` showed PP-OCR dropping the spaces in widely spaced table rows.
- **`tatr`** keeps Tesseract's text but finds tables with Table Transformer
  (PubTables-1M detection, threshold 0.8; v1.1-all structure recognition, rows ≥
  0.5) instead of ADR-038's stitching. Each table is read row by row from TATR's
  rows, a multi-line row line by line across. With no table found the output is the
  control's.
- **`ppstructure-v3`** is a whole other pipeline (PaddleOCR's layout detection, its
  own OCR, table recognition), run on the table and failure pages only.

Recognisers: `PP-OCRv6_small_rec` (the roadmap's candidate) and
`en_PP-OCRv5_mobile_rec` (PaddleOCR's English model). Environments are separate
venvs (PaddlePaddle 3.3.1 / PaddleOCR 3.7.0 / PaddleX 3.7.2; torch CPU 2.15 /
transformers 4.57.6: transformers 5.x rejects the TATR checkpoints' config). All 76
scored pages (72 scans); runs on a 4-vCPU, 16 GB box with up to three jobs side by
side, so seconds are rough.

## Recognisers

| Engine | wer | Δwer vs control | Δcer | better / worse | s/page | peak RSS |
|---|---|---|---|---|---|---|
| `rec-tesseract` (control) | 0.067 | — | — | — | 3.0 | 0.4 GB |
| `rec-PP-OCRv6_small_rec` | 0.177 | +0.110 | +0.012 | 18 / 42 | 11.9 | 2.2 GB |
| `rec-en_PP-OCRv5_mobile_rec` | 0.145 | +0.078 | +0.011 | 27 / 43 | 9.1 | 1.9 GB |
| `wrec-PP-OCRv6_small_rec` | 0.063 | −0.004 | −0.001 | 39 / 25 | 13.6 | 2.7 GB |
| `wrec-en_PP-OCRv5_mobile_rec` | 0.067 | +0.001 | −0.001 | 38 / 24 | 9.8 | 1.7 GB |

- **Line-level PaddleOCR loses on word boundaries, not letters.** wer rises ten
  times as much as cer. On table rows it writes `range42 5746 5934` for `range 42
  57 46 59 34`: the model drops wide gaps. On V2 p17 (typewritten table) wer 0.026
  → 0.512.
- **Word-level PP-OCRv6 reads slightly better than Tesseract.** 39 pages better,
  25 worse; best on pages with photographs (19 pages, −0.014) and the 150 dpi
  colour scans (−0.005). Held out: set F −0.006 (9 better, 5 worse), set E +0.004.
- **Typewriter faces: no help.** The 1963 patents (4 pages): control 0.106,
  PaddleOCR 0.110–0.134. The 1985 patent: 0.089 → 0.071–0.077, but the default
  (with ADR-041's band cleanup) already scores 0.068. The 1997–2000 typeset patents
  favour line-level PP-OCRv6 (0.083 → 0.057, 4 pages).
- **Cost.** 3–4× the time of the whole default page and 1.7–2.7 GB of PaddlePaddle
  next to a backend that today needs none. For a 0.004 wer gain that doesn't hold
  on set E, this is not worth adding to the default path. If it is revisited, the
  word-level form is the one to test, and only as an opt-in engine.

## Tables: Table Transformer

| Engine | wer | Δwer vs control | better / worse / same | s/page | peak RSS |
|---|---|---|---|---|---|
| `tatr` | 0.167 | +0.101 | 5 / 30 / 37 | 4.1 | 1.3 GB |
| `tatr`, development sets (A–D, L) | | +0.061 | 4 / 20 | | |
| `tatr`, held out (E, F) | | +0.134 | 1 / 9 | | |

**When it finds the right table, it fixes what ADR-038 can't.**

| Page | control | `tatr` | What |
|---|---|---|---|
| V5N1 p18 | 0.327 (ord 0.38) | **0.062** (ord 0.14) | two tables side by side: TATR finds both (each 40% of the width) and reads each in turn — the known failure |
| V1N3 p16 | 0.453 | **0.022** | psm 3 alone loses this table's order (the default's guard catches it: 0.022) |
| V2 p14 | 0.211 | **0.117** | table page (the default: 0.110) |
| V5N3 p24, V2N2 p10/p12/p16/p32, V4 p32 | | same | tables already read correctly |

**But at threshold 0.8 it calls many two-column pages tables.** On V4 p26 (prose
left, a fee table right) it boxed both columns as one table, and reading that row
by row interleaves the columns: wer 0.011 → 0.743 with word recall unchanged
(0.996), `ord` 0.0 → 0.947. The same on V2 p4, V4 p25, V2 p6, V2N2 p6, V4N1 p27,
WO06 p4 (a patent page with margin line numbers), P00 p15, P63a p7.

**No gate separates the two cases on this data.** The three big wins all score
1.00, but so do the two worst losses (V4 p26, V4N1 p27). ADR-038's own table
signal (a stitched group of cells) fires on 9 of the 72 pages, not on V5N1 p18,
and on no held-out page. Box width doesn't separate them either (wins at 0.39–0.72
of the page width, losses at 0.27–0.86).

So TATR is not shippable as a page-level switch. It is the strongest lead for the
side-by-side-table failure, and it belongs in roadmap 3.7 (region routing): a
detected table region needs a check that its text is tabular (short cells, numbers,
aligned columns) rather than prose (long lines, sentence punctuation) before it is
read row by row. That check must be designed on the development sets and held out
on E and F.

## PP-StructureV3

Run on the 20 table and failure pages only (set A V16 p90, V2 p17; B's and C's
table pages; D's tables plus V5N1 p18 and V5N3 p24; F's table pages; L's; and two
patent pages as a check), server models, 300 dpi, PaddlePaddle **3.2.2**: on
3.3.1 a oneDNN kernel of the pipeline raises `NotImplementedError`
(`ConvertPirAttribute2RuntimeAttribute`), and with oneDNN switched off one 150 dpi
page asked for an 11.4 GB allocation (the kernel killed the first attempt).

| Engine (same 20 pages) | wer | word recall | number recall | s/page | peak RSS |
|---|---|---|---|---|---|
| default (`pymupdf`) | **0.090** | 0.947 | 0.907 | ~7 | 0.4 GB |
| `rec-tesseract` (control) | 0.119 | 0.945 | 0.896 | 3 | 0.4 GB |
| `tatr` | 0.116 | 0.945 | 0.896 | 4 | 1.3 GB |
| `ppstructure-v3` | 0.332 | 0.874 | 0.819 | 30 | **8.0–10.6 GB** |

- Worse than the default on 18 of 20 pages. Better on P85 p6 (0.053 → 0.036) and
  V5N3 p19 (0.121 → 0.043).
- **It loses text**: word recall 0.874 (V2N2 p23 0.65, V2N2 p16 0.69, V2 p12 0.69).
- **It merges V5N1 p18's two side-by-side tables into one HTML table** (`mean 7.74
  mm 4.09 mm mean 10.8 mm 9.9 mm` on one row): the same failure PP-DocLayout showed
  inside GLM-OCR on V16 p90 (roadmap review notes). wer 0.417.
- V16 p90 (born-digital, text layer ignored): 0.925, the reading order of its
  directory table collapses (`ord` 0.92).
- Its Markdown carries tables as HTML; the scorer strips tags, so this is not
  what costs it.

Not a candidate: it is less accurate than the default on the pages it is meant for,
and one page needs half of a 16 GB Space.

## Memory on a 16 GB CPU Space

| Stack | Peak RSS | Notes |
|---|---|---|
| default path (Tesseract) | ~0.4 GB | |
| TATR (torch CPU) | 1.3 GB | |
| PaddleOCR recogniser | 1.7–2.7 GB | |
| PP-StructureV3, server models, 300 dpi | 6.3–10.6 GB | one page at a time; PaddlePaddle 3.2.2 |

## Environment notes for the next session

- `venv-paddle`: `pip install paddlepaddle==3.2.2 paddleocr==3.7.0 "paddlex[ocr]==3.7.2"`
  plus PyMuPDF; models download from Hugging Face (`PADDLE_PDX_MODEL_SOURCE=huggingface`).
  PaddlePaddle 3.3.1 runs the recognisers but not PP-StructureV3.
- `venv-tatr`: `pip install --index-url https://download.pytorch.org/whl/cpu torch
  torchvision`, then `transformers==4.57.6 timm opencv-python-headless` and PyMuPDF.
  The TATR pre- and post-processing is written out in `ml_engines.py` (Microsoft's
  reference: longest side 800 / 1000, ImageNet normalisation) because the image
  processor config shipped with the structure checkpoint does not load.
- Set `PADDLE_THREADS` / `TATR_THREADS` when running beside other jobs.
