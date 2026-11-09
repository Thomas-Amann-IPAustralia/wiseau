# Set S: the structural gold set and first structure scores (2026-11-09)

Roadmap Phase 14, item 2.3. No paid API was used; no engine was run except the
default path (`run_engines.py pymupdf --set s`, to cover V16 p3 and p97). The
docling, GLM-OCR and Gemini numbers are from outputs already in the repo.

## Why

The user's blind review of three frontier-model transcriptions
(`docs/Frontier-transcription-check.md`, five pages) concluded that *text
recognition is, for these documents, a solved problem; what remains is
reconstructing the document's structure and its syntax*: headings with levels,
tables that are tables, bold and italic where printed. "If we're working with
Markdown files we should be using Markdown syntax… otherwise it may as well be a
.txt file", and structure is what later chunking needs. `score.py` measures only
words, so none of that was measurable. Set S makes it measurable.

## The set

Twelve documents, 20 pages, 399 blocks, 43 relations
(`benchmarks/bakeoff/structure/`; format and protocol in its README). All five
pages of the user's review are in it (S01–S05). Annotated blind from page images,
with the text taken from the frozen blind references, and committed (`fb08ab2`)
before the scorer was run on it. Every gold file's words match its reference
token for token.

It covers side-by-side table panels, wide tables with wrapped cells, tables
continuing on the next page, captioned photographs, paragraphs continuing across
columns and pages, up to four heading levels, a complex contents page (V16 p3),
a footnote-heavy page (V16 p97, 69 markers) and a typewritten patent page.
**Gaps:** no deeply nested report and no table that repeats its header on the
next page; the corpus has neither.

## Metrics (`structure.py`)

Blocks are matched by their words (aligned in order, with extra passes for blocks
an output reorders). `blk_f` block F1; `typ_f` the same counting only type
agreement in the Markdown view; `split`/`merge` share of gold blocks an output
splits or merges; `ord` block order error; `hd_p`/`hd_r` heading precision and
recall; `lvl` heading level accuracy (relative ranks); `htree` heading-tree
similarity (Zhang–Shasha); `teds`/`teds_s` table TEDS with and without cell text,
per logical table (side-by-side panels and page continuations scored as one);
`em_r`/`em_p` bold/italic recall and precision outside headings; `rel_r`
relation recall. Checked by scoring each gold file against itself: perfect on
11 of 12 (`--self-check`; S05's page number "- 7" reads as a Markdown list item,
a known quirk of one frozen block).

## Results

The eight documents that the default path, docling and GLM-OCR all have
(S01–S03, S06–S09, S12):

| engine | blk_f | typ_f | split | merge | hd_p | hd_r | lvl | htree | teds | teds_s | em_r |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **default (pymupdf)** | 0.718 | 0.530 | 0.065 | 0.250 | 0.000 | 0.000 | – | 0.000 | 0.258 | 0.380 | 0.125 |
| default, its IR (before serializing) | 0.718 | 0.519 | 0.065 | 0.250 | 0.000 | 0.000 | – | 0.000 | 0.269 | 0.389 | 0.125 |
| docling | 0.732 | 0.667 | 0.050 | 0.131 | 0.765 | 0.865 | 0.299 | 0.485 | 0.513 | 0.684 | 0.000 |
| GLM-OCR | 0.646 | 0.606 | 0.063 | 0.135 | 0.845 | 0.801 | 0.315 | 0.521 | **0.717** | **0.814** | 0.000 |

All 12 documents, default path: blk_f 0.624, typ_f 0.456, hd_r 0.100, teds
0.279, em_r 0.196 (the emphasis it finds is on the born-digital pages). Gemini
3.5 Flash has an output for only S12 of these pages (blk_f 0.750, teds 0.375).
Per-document figures: `python benchmarks/bakeoff/structure.py --docs`;
`results-structure.json`.

## What it says

1. **The default path recovers no structure on scans.** It finds no heading on any
   scanned page (hd_r 0 on S01–S09), no table (TEDS 0.26: rows come out as text
   lines) and no emphasis. Its blocks are as good as docling's (blk_f 0.718 against
   0.732), but it merges more (0.250 against 0.131): blank-line splitting is the
   only structure Tesseract's output has.
2. **Nobody marks emphasis on scans.** em_r is 0 for every engine on every scanned
   document — the user's observation, now a number. The default path's 0.125–0.196
   comes from PyMuPDF4LLM on the born-digital pages.
3. **Heading levels are the remote engines' weak point.** docling and GLM-OCR find
   most headings (hd_r 0.80–0.87) but level them flat: every heading either
   writes on these pages is `##` (171 and 156 of them), so lvl ≈ 0.3 and the
   heading tree is half right (htree ≈ 0.5). Levels are the gap roadmap 3.1 fills.
4. **Tables are where the engines differ most.** GLM-OCR's HTML tables reach TEDS
   0.72 (1.00 on S09's fee table, 0.96 on S01's panels); docling 0.51; the default
   0.26.
5. **The IR alone barely helps tables (0.258 → 0.269).** The default path's
   stitched tables (ADR-038) keep rows, but their "cells" are Tesseract line
   fragments, not columns. So writing those tables as pipe tables (roadmap 2.2's
   dialect) would gain little by itself; real cells need a column/cell recogniser
   (Table Transformer, roadmap 3.7), and headings need 3.1.
6. **No engine states a relation** (rel_r 0): captions, continuations and
   footnotes exist only in the gold set so far.

## Reproduce

```bash
python benchmarks/bakeoff/run_engines.py pymupdf --set s   # only the default path needs running
python benchmarks/bakeoff/structure.py --docs
python benchmarks/bakeoff/structure.py --self-check
```
