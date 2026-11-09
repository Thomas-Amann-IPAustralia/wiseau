# Hand-over: text-layer boxes (ADR-044) and set S, the structural gold set (2026-11-09c)

Read this after `CLAUDE.md` and `STATUS.md`. The plan is still
[`roadmap-ocr-and-structure.md`](roadmap-ocr-and-structure.md). This session did
the first two items of [`handover-2026-11-09b.md`](handover-2026-11-09b.md)'s
list, in the order the user asked for them; that hand-over still holds the IR
design notes, and [`handover-2026-11-09.md`](handover-2026-11-09.md) the
environment rebuild. Branch: `ccr-f0a0e598-etgjpb` (main merged in).

**Standing user instruction: no paid API tokens.** Nothing here used one.

## The user's input this session

`docs/Frontier-transcription-check.md` (on main): a blind ranking of three
frontier-model transcriptions of five pages. Conclusion: text recognition is
good enough; **what matters now is structure** (headings with levels, tables that
are tables, bold and italic as printed), because the output is Markdown and will
be chunked. The hashes in it are of outputs made outside this repo; none matches
a file here. All five pages are in set S.

## What happened

1. **ADR-044 — boxes for text-layer blocks** (`parsers/text_layer.py`). Each
   PyMuPDF4LLM block's tokens are aligned with `page.get_text("words")` by
   SequenceMatcher, with repeated passes for blocks emitted out of page order
   (one pass recovers one crossed block, so passes continue until one aligns
   nothing, max 32). Element gets union box, the page lines it sits on,
   `bbox_signal="text-layer-alignment"`, `bbox_coverage`; **no box under 0.5**.
   99.3% of V16/V20's 4,605 blocks boxed; 49 of 42,705 same-page box pairs
   overlap (0.11%). Markdown untouched; time within noise.
2. **Set S — the structural gold set** (`benchmarks/bakeoff/structure/`, frozen
   in `fb08ab2`). 12 documents / 20 pages / 399 blocks / 43 relations. Gold is the
   *ideal Markdown* plus `<!-- type id= caption_of= continues_from= ... -->`
   annotations. Text from the frozen references (token-identical); structure from
   page images. README has format, protocol, gaps.
3. **`structure.py`** — block F1, typed F1, split/merge, block order, heading
   P/R, relative level accuracy, heading-tree similarity (Zhang–Shasha), TEDS per
   logical table, emphasis R/P, relation recall; `pymupdf-ir` scores the default
   path's IR JSON. `--self-check`: gold vs itself is perfect except one documented
   quirk (S05's "- 7" page number parses as a list item).
4. **Harness:** `regions.SAMPLE_S` / `run_engines.py --set s`; default-path
   outputs and IR for V16 p3 and p97 added; set A's V16 IR JSON refreshed with
   boxes.

Write-up: `docs/benchmarks/2026-11-09-structure-gold-set.md`. Tests: 555 passed,
8 skipped.

## Findings to carry forward

- **Default path on scans: no headings (hd_r 0), no tables (TEDS 0.26), no
  emphasis.** Its blocks are fine (blk_f 0.72, as good as docling).
- **docling and GLM-OCR write every heading as `##`** (lvl ≈ 0.3) — heading
  *levels* are open for everyone; roadmap 3.1.
- **GLM-OCR leads on tables** (TEDS 0.72 against docling 0.51), via HTML tables
  with `colspan` (the scorer expands spans to match the gold's convention).
- **Nobody marks emphasis on scans** (em_r 0 everywhere): the user's point.
- **The IR's stitched tables barely beat plain lines** (TEDS 0.269 vs 0.258):
  ADR-038 stitching groups *lines*, not columns. A pipe-table dialect alone will
  not move TEDS much; cell structure needs a recogniser (TATR, 3.7).
- Text-layer boxes fail where PyMuPDF4LLM's own blocks are wrong (V20 p173/p224
  split table rows) or text repeats (V16 p52–53 captions); both are visible in
  `bbox_coverage` and the overlap check in ADR-044.

## Pitfalls

- Never open an engine output for a page while annotating its gold; commit gold
  before scoring (the frozen commit is the proof).
- Gold tables without a printed header are HTML `<table>` (no invented header).
  Row labels printed across a table are a row with the label in the first cell.
- `structure.py` reads Markdown with `serializer.split_markdown`, so a block's
  type is what its Markdown says; an annotation-only type (caption, footnote,
  page furniture) is read as "paragraph" for `typ_f`.
- Gemini outputs exist for set A only (one set S document, S12).

## What to do next (the remaining two of the four)

1. **The explicit Markdown dialect** (2.2's second half; its own ADR; changes
   output). Score it on set S *and* `score.py`: headings and tables are where it
   can show up, but per the IR finding expect little from tables until 3.7.
   Bold/italic would need font information the OCR path does not have; on
   text-layer pages PyMuPDF4LLM already emits it.
2. **3.7 TATR region routing** as an IR pass, now measurable on set S's TEDS
   (design on A–D/L, hold out on E/F, check set S).
Then 3.1 heading hierarchy (set S's htree/lvl are the measure).
