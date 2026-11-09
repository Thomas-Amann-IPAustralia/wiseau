# The document IR is behaviour-neutral (2026-11-09, ADR-043)

Roadmap Phase 14, items 2.1–2.2. The roadmap's gate for replacing the string path
with an IR and a serializer: **the Markdown must match today's byte for byte.**
No paid API was used.

## What was compared

The *before* side is the code at `4c905a1` (the Tier 1 hand-over), run from a git
worktree; the *after* side is the IR refactor. Both ran the default path
(`engine="pymupdf"`, `WISEAU_OCR_MODE=auto`, Tesseract 5, 300 dpi), four
conversions at a time (`OMP_THREAD_LIMIT=1` is set by the code since ADR-042).

| Check | What it exercises | Result |
|---|---|---|
| All 15 test PDFs, each converted **whole** | 859 pages: 382 OCR'd (scans, image-only and Acrobat-layer pages), 477 from a text layer. V20 takes the fast path (one PyMuPDF4LLM call, now with `page_chunks=True`); V16 the mixed path (106 text-layer pages, 2 OCR'd); the rest the all-OCR path | **15 / 15 identical** (SHA-256 equal) |
| A synthetic 8-page PDF interleaving V16, V20 (text layer), V5N1 (image-only), V1N3 and V2N2 (Acrobat layer) pages | The mixed path with page-by-page PyMuPDF4LLM and OCR alternating | **identical** |
| Every scored benchmark page (sets A, B, C, D, E, F, L), cut to a one-page PDF as `run_engines.py` does | Against the committed `benchmarks/bakeoff/outputs/pymupdf/*.md` | **76 / 76 identical** |
| The IR JSON of those 76 pages, rebuilt and serialized | `to_dict()` holds everything the serializer needs | **76 / 76 identical** |
| Time, whole corpus | | unchanged: every document within 10% of its before time, 14 of 15 equal or faster (machine noise) |

The unit suite adds a property test: 2,000 seeded random Markdown strings, split
into blocks and re-joined, normalize to the same string as the original. The one
exception it found, a whitespace-only document (now `""` rather than `"\n"`), is
pinned in the test; every caller treats both as "no text".

## What the IR holds on the default path

On the 76 scored pages: 1,278 elements. 1,189 come from `tesseract-psm3` (1,145
`text` blocks with every line's box, 44 row-stitched `table`s carrying their
cells), 88 from `pymupdf4llm` (85 `paragraph`, 3 `heading`; no boxes, as
PyMuPDF4LLM's Markdown reports none), and 1 from `mupdf-ocr`: one page where
ADR-038's guard text won, in MuPDF's own order. No scored page used the guard's
ADR-035 column order (`tesseract-geometric`). `run_engines.py pymupdf` now writes each page's
IR next to its Markdown (`outputs/pymupdf/<page>.json`).

## Reproduce

```bash
git worktree add /tmp/base 4c905a1
python benchmarks/bakeoff/whole_docs.py /tmp/base/backend before pdf-test/*.pdf pdf-test/Extra-4-Broad-Test/*.pdf
python benchmarks/bakeoff/whole_docs.py backend after pdf-test/*.pdf pdf-test/Extra-4-Broad-Test/*.pdf
for f in before/*.md; do cmp "$f" "after/$(basename "$f")"; done
python benchmarks/bakeoff/run_engines.py pymupdf --set b   # ... each set; then git diff outputs/pymupdf/*.md
```
