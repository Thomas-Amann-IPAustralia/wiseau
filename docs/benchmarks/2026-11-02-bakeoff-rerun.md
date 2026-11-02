# Engine bake-off, re-run — 2026-11-02

The [2026-11-01 bake-off](2026-11-01-engine-bakeoff.md) scored GLM-OCR while it was
still running with the SDK's `repetition_penalty=1.1`. The
[diagnosis](2026-11-02-glm-ocr-diagnosis.md) found that this setting caused its
runaways and changed the default to 1.0. This re-run scores the three self-hosted
engines again on the same 10 pages, with GLM-OCR at the new default.

**No paid or external API was called.** The cloud rows (Gemini, GPT) from 11-01 were
not re-run, and they are not comparable with the numbers below (see *Caveats*).

Everything needed to reproduce this run is committed this time, in
[`benchmarks/bakeoff/`](../../benchmarks/bakeoff/): the references, each engine's
raw output, per-page timings, `results.json`, and the scripts.

## Setup

- **Hardware:** 4 vCPU Xeon @ 2.1 GHz (AMX, bf16), 15 GB RAM, no GPU. This is the
  same class of box as the 11-01 run.
- **One engine at a time, one page at a time:** each page is cut into its own
  single-page PDF and converted alone. Nothing else ran during the timed runs.
- **Engines:**
  - **PyMuPDF + Tesseract:** wiseau's default path, `file_to_markdown(engine="pymupdf")`.
  - **docling 2.145.0:** the library, with EasyOCR and table structure on. Models were
    loaded once before timing.
  - **GLM-OCR:** `glm-ocr/service.py` + `model_server.py`, unchanged from `main`
    (bf16, 4 threads, `GLM_OCR_REPETITION_PENALTY` at its new default of 1.0), with
    `glm-ocr/requirements.txt` and the revision-pinned weights.
- **Sample:** V16 p11/25/34/90 (born-digital), V2 p6/17/20 and V4 p7/11/26 (scanned).
- **References** (`benchmarks/bakeoff/references/`):
  - **V16:** the PDF text layer, read in the reading order defined by the regions in
    `regions.py`. Table rows are read across.
  - **Scans:** a Tesseract draft of each region, corrected by hand against zoomed page
    images. Text printed inside photographs is left out.
- **Metrics** (`score.py`): text is lower-cased, and markup and LaTeX commands are
  stripped before scoring. Lower `wer` is better; for every other column, higher is
  better.
  - **`wer`:** word edit distance divided by reference length. Reading order counts.
  - **`bow_r` / `bow_p`:** word recall / precision, ignoring order.
  - **`num_*`:** the same as `bow_*`, but only for tokens that contain a digit.
  - Group figures are the mean over that group's pages.

## Results

| Engine | Pages | wer ↓ | bow_r | bow_p | num_r | num_p | s/page |
|---|---|---|---|---|---|---|---|
| PyMuPDF + Tesseract — digital | 4 | **0.019** | **0.995** | **0.993** | **1.000** | **1.000** | 0.2 |
| docling — digital | 4 | 0.487 | 0.982 | 0.987 | 0.964 | 0.993 | 22 |
| GLM-OCR — digital | 4 | 0.297 | 0.896 | 0.946 | 0.937 | 0.911 | 205 |
| PyMuPDF + Tesseract — scanned | 6 | 0.648 | **0.930** | 0.945 | 0.845 | 0.908 | 5 |
| docling — scanned | 6 | 0.213 | 0.841 | 0.928 | 0.684 | **0.912** | 41 |
| GLM-OCR — scanned | 6 | **0.103** | **0.930** | **0.956** | **0.895** | 0.892 | 106 |

Per page (wer / bow_r / num_r):

| Page | PyMuPDF + Tesseract | docling | GLM-OCR | GLM-OCR s |
|---|---|---|---|---|
| V16 p11 (dotted-leader index) | 0.064 / 0.988 / 1.000 | 0.924 / 0.980 / 0.966 | 0.147 / 0.917 / 0.966 | 136 |
| V16 p25 (table + 2 columns) | 0.012 / 0.993 / 1.000 | 0.066 / 0.971 / 0.950 | 0.161 / 0.839 / 0.950 | 131 |
| V16 p34 (2-column prose) | 0.000 / 1.000 / 1.000 | 0.025 / 0.985 / 0.947 | 0.017 / 0.990 / 0.947 | 109 |
| V16 p90 (2 side-by-side tables) | 0.000 / 1.000 / 1.000 | 0.933 / 0.990 / 0.991 | 0.863 / 0.839 / 0.883 | 444 |
| V2 p6 (2-column prose) | 0.733 / 0.986 / 0.953 | 0.019 / 0.988 / 0.930 | 0.016 / 0.997 / 0.953 | 73 |
| V2 p17 (typewriter table) | 0.036 / 0.982 / 0.963 | 0.704 / 0.559 / 0.421 | 0.462 / 0.621 / 0.607 | 236 |
| V2 p20 (2 columns + list) | 0.658 / 0.989 / 0.964 | 0.033 / 0.978 / 0.982 | 0.032 / 0.994 / 0.982 | 82 |
| V4 p7 (prose + faint table + photo) | 0.794 / 0.894 / 0.653 | 0.325 / 0.678 / 0.408 | 0.029 / 0.995 / 0.980 | 123 |
| V4 p11 (photo + table + prose) | 0.905 / 0.736 / 0.558 | 0.171 / 0.855 / 0.423 | 0.027 / 0.973 / 0.865 | 80 |
| V4 p26 (2 columns + fee list) | 0.761 / 0.993 / 0.980 | 0.025 / 0.989 / 0.939 | 0.054 / 0.996 / 0.980 | 43 |

GLM-OCR wrote 172 region requests. One hit its token limit: a 10-token request whose
limit the SDK sets itself, not a runaway. No region looped.

## What changed for GLM-OCR since 11-01

The references were rebuilt, so the two runs are not scored against identical text.
The PyMuPDF and docling rows barely moved, though, so the scales line up:

- **PyMuPDF, scanned:** wer 0.676 → 0.648, bow_r 0.930 → 0.930, num_r 0.837 → 0.845.
- **docling, scanned:** wer 0.221 → 0.213, bow_r 0.852 → 0.841, num_r 0.680 → 0.684.

GLM-OCR moved a lot:

| GLM-OCR | wer | bow_r | bow_p | num_r | num_p |
|---|---|---|---|---|---|
| scanned, 11-01 (penalty 1.1, 6 pages) | 0.110 | 0.912 | 0.948 | 0.744 | 0.744 |
| scanned, 11-02 (penalty 1.0, 6 pages) | **0.103** | **0.930** | **0.956** | **0.895** | **0.892** |
| digital, 11-01 (penalty 1.1, 2 pages) | 0.280 | 0.745 | 0.948 | 0.810 | 0.907 |
| digital, 11-02 (penalty 1.0, 4 pages) | 0.297 | **0.896** | 0.946 | **0.937** | 0.911 |

**The big gain is numbers.** The share of the page's numbers that GLM-OCR found went
from 0.74 to 0.90. The share of its numbers that were really on the page also went
from 0.74 to 0.89. Per page:

- **V4 p7:** 0.74 → 0.98.
- **V4 p11:** 0.49 → 0.87.
- **V2 p17:** 0.46 → 0.61.

This fits the cause the diagnosis found. A repetition penalty pushes the model away
from any token it has already written, and digits and units repeat constantly in
tables. At 1.1 the model was being steered away from the right digits.

**The V16 p11 index is now complete** (word recall 0.60 → 0.92). The remaining loss
there is the page-number shift the diagnosis described, plus the PBR symbol coming out
as LaTeX.

## Findings

1. **Scanned pages: GLM-OCR is now the best of the three self-hosted engines, or tied
   for best, on every column except number precision.**
   - It keeps reading order on two-column scans (wer 0.10, against 0.21 for docling and
     0.65 for Tesseract).
   - It finds as many words as Tesseract and more numbers than either other engine.
   - On V4 p7 and p11, where a faint table sits next to a photograph, it is the only
     engine that does well (number recall 0.98 / 0.87; docling 0.41 / 0.42; Tesseract
     0.65 / 0.56).
2. **The typewriter table (V2 p17) is still poor for both ML engines.**
   - GLM-OCR's word recall there is 0.62 (docling 0.56). Much of that loss is GLM-OCR
     **dropping the spaces inside table cells**: `9.4cm`, `lightrose`,
     `PLANT:GROWTHHABIT` on V16 p25. The words are read but come out glued together,
     which a reader or a search would miss.
   - Tesseract reads this table almost perfectly in row order (wer 0.04) because it is
     a plain grid of monospaced type.
3. **Side-by-side tables (V16 p90) are still broken for both ML engines.** GLM-OCR's
   layout step treats both tables as one region and mis-pairs the rows. It also wrote
   phone numbers that aren't on the page (`num_p` 0.88) and dropped the page header.
   docling interleaves the two tables differently (wer 0.93).
4. **Born-digital pages: keep PyMuPDF.**
   - It is exact (wer 0.02, every number found and correct) and takes 0.2 s per page.
   - GLM-OCR re-reads the page from pixels: 0.90 word recall at about 3.5 minutes per
     page.
   - docling has the words but scrambles index and table pages (wer 0.92–0.93 on p11
     and p90).
5. **Speed:**
   - GLM-OCR averages 106 s per scanned page and 205 s per digital page (43–444 s).
   - That is about 2.5× docling and about 20× PyMuPDF + Tesseract on scans.

## So which engine is doing better?

| Document | Use | Why |
|---|---|---|
| Born-digital PDF | **PyMuPDF** (the default) | Exact, instant. |
| Scanned, mostly prose or multi-column | **GLM-OCR** | Best reading order and best number recall. |
| Scanned, with faint or mixed tables | **GLM-OCR** | Only engine that reads V4 p7/p11 well. |
| Scanned, plain typewriter grid table | **PyMuPDF + Tesseract** | Both ML engines lose a third of it. |
| Side-by-side tables | None of them | Only the text-layer path gets it right. |

GLM-OCR at penalty 1.0 is a clear improvement on the 11-01 result. It is now the best
self-hosted engine for scans, provided waiting minutes per page is acceptable.

## Caveats

- **Small sample:** 10 pages, one run each. The ML engines are stochastic by design
  (ADR-013), so expect small run-to-run movement.
- **Scanned references:** drafted from Tesseract and then hand-corrected. If anything,
  that biases toward Tesseract.
- **The 11-01 cloud rows** (Gemini 3.8 Flash, GPT-5.4) were scored against the old,
  uncommitted references, and their outputs were not kept, so they cannot be re-scored
  on this scale. On 11-01 Gemini Flash was more faithful than every self-hosted engine
  on scans (wer 0.010, number recall 0.99).
- **docling setup:** run as the 2.145 library, not the pinned `docling-serve` image the
  deployment uses. The image could not be built here because there is no Docker daemon.
- **Bug found while setting up:** the `glm-ocr` Docker image would not have booted
  offline. The weights were downloaded by commit hash, which records no `refs/main`,
  so `HF_HUB_OFFLINE=1` could not resolve the model names. The Dockerfile now writes
  `refs/main` for both pinned models. Still not verified with a real `docker build`.
