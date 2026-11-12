# A fast engine: Tesseract psm 3 alone, a cheap guard, 300 dpi (2026-11-12)

**Verdict.** `engine="fast"` reads a scanned page in **2.8 s against 7.7 s** for
the image's default path on 4 CPUs (4.9 against 11.3 s on 1 CPU). It keeps
**exactly the default path's words**: word recall 0.966 on both, 72 scans. It
loses what the two models add on scans: heading levels and rebuilt tables. A
lower DPI is not worth it: 200 dpi saves 0.3 s a page for 40% more word errors.
Peak memory is 0.44 GB against 2.2 GB. Decision: [ADR-059](../decisions.md).

Along the way, a fix that applies to both engines: psm 3 now really overlaps
MuPDF's OCR. Before, it waited for the GIL. This made the default path about
0.5 s a page quicker, with byte-identical output.

## Method

- **Pages:** sets A–F and L (`regions.py`): 72 scans and 4 born-digital pages,
  the same ones the preprocessing and recogniser studies used. Each page is
  converted on its own by the backend's real `file_to_document`, with
  `run_engines.py`. Runs went one after another on a quiet 4 vCPU box (Xeon,
  15 GB, no GPU).
- **Engines:**
  - `image-default`: the Docker image's default path exactly. That is
    `engine="pymupdf"` with `WISEAU_TABLES=tatr`, `WISEAU_LAYOUT=ppdl`,
    `WISEAU_STRUCTURE=all` and one thread per model. This is "Accurate".
  - `fast-GUARD-DPI`: `engine="fast"`, with the same environment, so the
    structure passes run. GUARD is `cheap` (what ships), or `none` for psm 3
    alone.
- **Scoring:**
  - Words: `score.py` against the hand-checked references (wer, cer, word and
    number recall).
  - Structure: `structure.py` on set S, the structural gold set (12 documents,
    20 pages).
  - Seconds are the conversion's wall time per page, with models loaded.
- **The guard survey:** `fast_guard.py`, run on the 336 scanned pages of the
  ADR-042 guard cache (all 16 test PDFs). For each page it records psm 3's
  plausible word count, the full 300 dpi guard's verdict, and MuPDF's OCR at 100
  and 150 dpi. Its seconds are rough, because 4 pages ran at once. Output:
  `benchmarks/bakeoff/survey/fast-guard-2026-11-12.json`.

## 1. The guard cannot be dropped, and it need not cost 4.5 s

The default path keeps MuPDF's OCR text as a guard. If psm 3 has under 85% of
the guard's plausible words, the guard's text is used (ADR-038/042).

- **How often the guard wins:** on 27 of the 336 pages.
- **How much psm 3 alone loses there:** up to a third of the page. The 2000
  patent's pages 5–8 and 12 lose 67–114 words each; V4 p32 loses 90 of 195.
- **Scored the same way:** psm 3 alone (`fast-none-300`) loses 47% of V4 p32's
  words. Its mean wer is 0.0625 against 0.0561.

**A lower resolution does not make that read cheap.** On V4 p7, MuPDF's OCR took
5.4 s at 300 dpi, 4.4 s at 200, 4.2 s at 150 and 3.3 s at 100. What lower
resolution does preserve is the *count*: the 100 dpi read finds about as many
plausible words as the 300 dpi one. A count is all the guard needs to decide.

So the fast engine counts psm 3 against a 100 dpi read, with the same 85% rule.
Only a page that fails gets the full 300 dpi guard and the default path's rule.

| Cheap read | Guard wins caught (of 27) | Full guard run needlessly | Largest loss missed |
|---|---|---|---|
| 100 dpi | 16 | 0 | 34 words (P85 p2) |
| 150 dpi | 20 | 1 | 34 words (P85 p2) |

- **Every page losing 37 or more words is caught at 100 dpi.** That includes
  all the patent pages and V4 p32.
- **The 11 misses lose 4–34 words each.** Most are cover and back pages.
- **On two of the misses the guard's text is worse anyway.** ADR-042 judged
  P85 p25 and P97 p23 that way.
- **Why not 150 dpi:** it catches 4 more of these small losses, for about 45%
  more time on every page.
- **Stricter shares at 100 dpi (90–95%) don't help:** they catch 1–3 more small
  pages and run the full guard needlessly on 2–12.

## 2. psm 3 was not running beside the guard (fixed for both engines)

The first timed run of the fast engine came in at 4.6 s a page, not the expected
~3. The cause:
- MuPDF's `get_textpage_ocr` holds Python's GIL for its whole run.
- The worker thread that was to launch psm 3, and feed it the page image over
  stdin, could not run until the guard finished. So the two ran one after the
  other on every scanned page, on both engines.
- ADR-056's overlap still paid off on the default path, because torch releases
  the GIL during the models' inference.

**The fix:** `tesseract_layout.start_tsv` launches the process on the calling
thread, before the guard. The image goes in and the TSV comes out through
temporary files, so the process needs nothing more from Python.
- On V4 p7, guard and psm 3 together went from 5.9 s to 3.3 s.
- The TSV is byte-identical to the stdin path.
- **Default path:** re-run on all 76 pages, the Markdown and IR are
  byte-identical (only `timings.json` and `runs.json` changed). Mean time per
  scanned page went from 8.2 to 7.7 s.
- **Fast engine:** re-run, all 76 pages are byte-identical to its first run.

## 3. Words, time and memory (72 scans)

| Engine | wer | cer | word recall | number recall | s/page (median, max) | peak RSS |
|---|---|---|---|---|---|---|
| Accurate (`image-default`) | 0.0678 | 0.0355 | 0.9658 | 0.8977 | 7.70 (7.40, 13.4) | 2.2 GB |
| **Fast, cheap guard, 300 dpi** | **0.0561** | **0.0231** | **0.9658** | **0.8977** | **2.77** (2.80, 4.5) | 0.44 GB |
| Fast, cheap guard, 200 dpi | 0.0787 | 0.0374 | 0.9578 | 0.8679 | 2.47 (2.40, 4.2) | 0.39 GB |
| Fast, cheap guard, 150 dpi | 0.0996 | 0.0461 | 0.9384 | 0.8318 | 2.28 (2.25, 3.8) | 0.40 GB |
| psm 3 alone, no guard, 300 dpi | 0.0625 | 0.0264 | 0.9593 | 0.8855 | 2.67 (2.75, 4.4) | 0.37 GB |

- **The same words as Accurate.** Fast's word and number recall are identical
  to Accurate's on every page, and the cheap guard reached the same choice as
  the full one on all 72. Born-digital pages: wer 0.019 on both, 0.2 s.
- **Fast's wer is *lower* than Accurate's.** Accurate's layout pass regroups
  lines into the detector's regions, and its table pass reads tables cell by
  cell. The references are written line by line, with table rows read across,
  so those regroupings count as order errors (`ord` 0.088 against 0.102). On
  the structure set (§4) the same regroupings are what make Accurate better. So
  this is a property of the references, not a fidelity loss in Accurate.
- **Lower DPI loses words and saves little.**
  - 200 dpi: wer +40%, 0.3 s saved, word recall down 7 points on V2 p17 (a
    typewritten table).
  - 150 dpi: wer +78%, 0.5 s saved, V2 p17 down 22 points.
  - The 300 dpi psm 3 pass is about 2.2 s of the 2.8.
- **One CPU** (`taskset -c 0`, sets A and B, 16 scans): Accurate 11.3 s,
  Fast 4.9 s. Both are byte-identical to their 4-CPU runs. Fast gains less
  here (2.3× against 2.8×) because with one core nothing overlaps.

## 4. Structure (set S)

The 8 documents that every engine has (S01–S03, S06–S09, S12):

| Engine | blk_f | typ_f | merge | hd_p | hd_r | lvl | htree | teds | teds_s |
|---|---|---|---|---|---|---|---|---|---|
| **Accurate (`image-default`)** | **0.817** | **0.720** | **0.110** | **0.786** | 0.765 | **0.728** | **0.674** | 0.572 | 0.702 |
| Fast | 0.717 | 0.528 | 0.250 | 0.000 | 0.000 | – | 0.000 | 0.258 | 0.380 |
| docling | 0.732 | 0.667 | 0.131 | 0.765 | **0.865** | 0.299 | 0.485 | 0.513 | 0.684 |
| GLM-OCR | 0.646 | 0.606 | 0.135 | 0.845 | 0.801 | 0.315 | 0.521 | **0.717** | **0.814** |

All 12 documents: Accurate block F1 0.799, Fast 0.659.

- **Fast has no headings on scans** and writes tables as rows of text, like the
  default path before ADR-046/048. Heading levels on born-digital pages still
  come from the text layer and the structure passes.
- **Accurate, as shipped since ADR-055, beats docling on structure** on these
  documents, everywhere except heading recall. docling finds more headings
  (0.865 against 0.765) but flattens their levels. GLM-OCR's tables are still
  the best (TEDS 0.72).
- **These are per-page conversions.** Cross-page relations, such as
  continuations and continued tables, need whole documents, so they are not
  scored here.

## Honest limits

- **One corpus.** These are Australian PBR journals and patent specifications,
  mostly typeset or typewritten bilevel scans. The guard rule was chosen on all
  336 surveyed pages; the 72 scored pages are among them.
- **One machine, measured locally.** Cloud Run's CPUs are slower: ADR-048 saw
  22 s a warm scanned page there against 13 s locally. The live ratio has not
  been measured.
- **Handwriting is untested.** `WISEAU_OCR_ENGINE=easyocr` is not changed by the
  fast engine: `fast` always uses Tesseract.
- **What "the same words" rests on:** the cheap guard agreeing with the full
  guard on these pages. Over the 336-page survey, it can leave a page with up to
  34 fewer words than the full guard would.

## Reproduce

```bash
cd backend && pip install -r requirements.txt -r requirements-dev.txt
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 torchvision==0.29.2
pip install -r requirements-tables.txt
HF_HOME=/tmp/hf python fetch_table_models.py parsers/tables.py:2 parsers/layout.py:1
cd ..
export HF_HOME=/tmp/hf HF_HUB_OFFLINE=1
python benchmarks/bakeoff/fast_guard.py benchmarks/bakeoff/survey/fast-guard-2026-11-12.json --jobs 4
for e in image-default fast-cheap-300 fast-cheap-200 fast-cheap-150 fast-none-300; do
  for s in a b c d e f l; do python benchmarks/bakeoff/run_engines.py $e --set $s; done
done
for e in image-default fast-cheap-300; do python benchmarks/bakeoff/run_engines.py $e --set s; done
for e in image-default@1cpu fast-cheap-300@1cpu; do
  for s in a b; do taskset -c 0 python benchmarks/bakeoff/run_engines.py $e --set $s; done
done
python benchmarks/bakeoff/score.py && python benchmarks/bakeoff/structure.py
```
