# Hand-over: the table dialect (ADR-045) and Table Transformer tables (ADR-046) (2026-11-09d)

Read this after `CLAUDE.md` and `STATUS.md`. The plan is still
[`roadmap-ocr-and-structure.md`](roadmap-ocr-and-structure.md). This session did
items 3 and 4 of [`handover-2026-11-09c.md`](handover-2026-11-09c.md)'s list.
Branch: `ccr-f0a0e598-etgjpb`.

**Standing user instruction: no paid API tokens.** Nothing here used one.
**Open decision for the user:** whether to turn `WISEAU_TABLES=tatr` on in the
deployed Space (recommended; see `docs/benchmarks/2026-11-09-tables.md`).

## What happened

1. **ADR-046 — `parsers/tables.py`** (opt-in, `WISEAU_TABLES=tatr`; PyTorch etc.
   in `requirements-tables.txt`, imported lazily). An IR pass inside
   `TesseractEngine.read_elements`, only on psm 3's reading:
   `detect(page)` (TATR detection ≥ 0.8 + structure, the expensive half) →
   `regions_from(detections, words, present)` (cells from psm 3's words, row
   splitting, the check) → `apply(elements, regions, words)`.
   `tesseract_layout.read_page_layout` now returns blocks *and* words
   (`Word` with its (block, par, line) key); `read_page_blocks`/`read_page` stay.
2. **ADR-045 — the dialect** (`serializer.table_markdown`): real-cell tables as
   pipe tables (header row, no spans) or HTML; stitched rows stay plain
   (`STITCHED_AS_TABLES`). IR: `Cell.header`, `Element.score` (uncalibrated).
3. Results: set S TEDS 0.258 → 0.558; no word changes anywhere; set D wer
   0.072 → 0.052; held-out check clean; +26% per scanned page on one thread;
   peak RSS 0.28 → 1.17 GB. Off: byte-identical (15 PDFs whole + every page).
4. Tests: 566 pass, 8 skipped (`tests/test_tables.py` uses synthetic TATR
   structure, so CI needs no PyTorch).

## Harness

| Script | What |
|---|---|
| `tatr_regions.py OUT.json SETS` | Every detected region: features, and its effect on the page's wer alone |
| `tatr_eval.py cache DIR SETS` / `eval DIR` | Cache OCR + TATR per scan once (pickle, local only); score a grid of settings on words (dev/held out) and set S structure in minutes |
| `table_timing.py OUT.json --threads N` | Off/on seconds per page, TATR's share, model load, RSS; run alone |
| `run_engines.py tables-tatr` | End to end; `outputs/tables-tatr/` (md + IR JSON) for all sets and set S |

## Findings and pitfalls

- **Decide table membership word by word**: psm 3 reads lines straight across
  side-by-side tables; line-level membership left V5N1 p18 unfixed.
- **TATR rows are coarse**: one row around "mean / range / std deviation". Split
  only when 2+ cells stack the same number of lines, counted per column (skew).
- **TATR's "projected row header" misfires on data rows**: only trust it when the
  row's words run on without a column-sized gap.
- **Spanning cells hurt** on the development sets; they are off (`SPANS`).
- **wer punishes cell order**: a wrapped cell read as one cell differs from the
  line-by-line references. Check word multisets (identical here) before reading a
  wer rise as damage; use set S's TEDS for structure.
- The coarse-row problem was first noticed on held-out V2N2 p10; settings were
  still chosen on development numbers only. Set S is mostly development pages.

## What to do next

1. **The user's decision on deployment.** If yes: Dockerfile installs CPU torch
   + `requirements-tables.txt`, pre-downloads the two pinned models at build
   time, sets `WISEAU_TABLES=tatr`; then a live check on the Space (time, RSS).
2. **3.1 heading hierarchy** (set S: the default path finds no heading on scans;
   remote engines flatten all to `##`).
3. Tables TATR misses (S08, S09) and born-digital tables (V16 p90) are the next
   table gaps; spanning cells need a better mapping before they help.
