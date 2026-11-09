# The ADR-038 guard counts plausible words; Tesseract on one thread (2026-11-09)

Hand-over 2026-11-08c, item 0. No paid APIs. Decision: [ADR-042](../decisions.md).
Code: `backend/parsers/tesseract_layout.py`, `backend/parsers/ocr.py`.

## 1. The guard

ADR-038 reads a scan with Tesseract's own layout (psm 3) but keeps MuPDF's OCR text
as a guard: if psm 3 has under 85% of the guard's words, psm 3 probably threw a
column away and the guard is returned. "Words" were every run of letters and
digits, so speckle counted. On P85 p5 (the cover form) the guard read 243 runs out
of a stamp box against psm 3's 192, and the guard's worse text won, losing
"Address of Applicant: New Murphy Road and Concord Pike" (found by ADR-041).

**Rule.** Count a token if it contains a digit, or is a common short word
(`photo_regions.SHORT_WORDS`: a, of, mm, …), or has ≥ 3 letters, ≥ 2 different
letters, a vowel and no capital straight after a small letter. Junk such as `ee`,
`eee`, `Ss`, `NaS`, `oO` drops out; `snes`, `bago` still count (the rule does not
need to be perfect, only to stop speckle deciding the choice).

**Method.** `benchmarks/bakeoff/guard_cache.py` stores both texts the default path
chooses between (after band cleanup and the photo mask, exactly as
`TesseractEngine` computes them) for **every scanned page of the 16 test PDFs: 336
pages**. `guard_eval.py` lists the pages where the two counts choose differently
and scores both texts against the blind reference, or the page's own hidden text
layer (a silver reference) when there is none. The cache is archived as
`benchmarks/bakeoff/survey/guard-cache-2026-11-09.json.gz`.

**Results.** The choice flips on 11 of 336 pages; none is a scored page.

| Page | Old pick → new | Evidence | Verdict |
|---|---|---|---|
| P85 p5 | guard → psm 3 | silver wer 0.773 → 0.494; the address line is back | better |
| P85 p8 | guard → psm 3 | silver 0.554 → 0.114 | better |
| P85 p22 | guard → psm 3 | silver 1.181 → 0.989 | better |
| V4N1 p19 | guard → psm 3 | by eye: junk dropped, page number "17" kept | better |
| V5N1 p1 | guard → psm 3 | by eye: gains "Plant Varieties Journal" | better |
| V5N1 p17 | guard → psm 3 | by eye: drops `SE essere Te se ee eee …` | better |
| V5N1 p31 | guard → psm 3 | by eye: back cover; gains the printer's imprint, both read the rotated text as junk | better |
| V5N3 p1 | guard → psm 3 | by eye: gains "Plant Varieties Journal" | better |
| P85 p25 | psm 3 → guard | silver 0.670 → 1.064 (recall 0.638 → 0.723) | worse; junk either way (neighbouring-page strip, signature) |
| P97 p23 | psm 3 → guard | silver 0.574 → 0.611 | worse, marginally; a chemical structure drawing |
| P00 p41 | psm 3 → guard | 7 words | worse; loses a "FIG. 3B" label |

End to end with `silver_layer.py … PLAUSIBLE_GUARD` on P85's 23 unreferenced
pages: **wer 0.440 → 0.417, recall 0.851 → 0.858, precision 0.772 → 0.793**.

The rule was written looking at P85 p5, so P85 is development data. The other 310
pages are the held-out check: 7 flip, 5 for the better.

## 2. The timeout: output that depended on the machine's load

Building the cache with installs running beside it, psm 3 returned `None` (timed
out at `TIMEOUT_S` = 120 s) on five set F pages: V2N2 p32, V4N1 p7, p21, p22, p25.
Re-run on a quiet box they took about 10 s each. On a timeout the default returns
the guard text, so **a busy machine changed the Markdown**. The live test
`test_a_rendered_list_reads_across_each_row` also failed this way while the cache
ran.

Cause: the Ubuntu `tesseract` binary links OpenMP (`libgomp`) and spreads each page
over every core. Several runs at once oversubscribe the CPU, and OpenMP's spinning
threads make that far worse than linear (one manual run of V5N1 p18 used 13 CPU
minutes). The service runs up to `MAX_CONCURRENT_JOBS` = 4 conversions at once.

Fix: `read_page` runs the CLI with `OMP_THREAD_LIMIT=1` (`TESSERACT_ENV`).

| Check | Result |
|---|---|
| psm 3 text, one thread vs OpenMP, 12 pages (V2, V4, V5N1, V5N3, V2N2, V4N1, P85, P63a, P97, P00, WO06) | byte-identical on all 12 |
| psm 3 time per page, one thread | 2.0–5.0 s (measured with the cache job running; not compared with OpenMP on a quiet box) |
| Default path, all 76 scored pages, run with three other jobs on the box | byte-identical to the committed outputs; no timeout; mean 7.8 s/page (6.8 s committed, on a quiet box) |

MuPDF's own OCR (the guard pass) is linked into PyMuPDF and is not affected.

## Checks

- 532 tests pass, 8 skipped (3 new: plausible counting, the P85 p5 case with the
  raw-count switch, the one-thread environment).
- Deterministic: a pure function of the two texts; one-thread Tesseract gives the
  same text as before.
