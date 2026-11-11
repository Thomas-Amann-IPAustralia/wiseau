# Structure recovery: roadmap Tier 3 (2026-11-11)

The user asked for the Tier 3 work in `roadmap-ocr-and-structure.md`: heading
hierarchy (3.1), page furniture (3.2), paragraph continuation (3.3), figures as
assets (3.4), footnotes (3.5) and cross-page tables (3.6). No paid API was used.
ADR-049 to ADR-054 record the decisions; tech-spec §20 the behaviour.

## What was built

Six deterministic passes over the document IR (`backend/parsers/recover.py` and
one module each), opt-in through `WISEAU_STRUCTURE`. They change types and
levels, add relations, and in one case (footnotes) split a block at line
boundaries. They never edit text, and every inference names its signal. Reader
mode (`WISEAU_FURNITURE=drop`) leaves furniture out and writes a repeated table
header once. `POST /convert/bundle` (API 0.15.0) returns `document.md`,
`document.json` and `assets/*.png`.

## How it was measured

`benchmarks/bakeoff/recover_eval.py` rebuilds each gold document's IR from saved
outputs, using the image's path (tables + layout regions, re-applied with the
production `layout.apply`; 30 of 31 OCR'd pages reproduce `outputs/layout-ppdl/`
exactly, V4 p27 differs by ADR-048's known one-line rounding). It runs the
passes, writes Markdown as production does, and scores:

- `structure.py`'s block and heading metrics;
- `layout_regions.label_scores` for the IR-only types;
- **relations**: a gold `caption_of` / `continues_from` / `continues_table` /
  `footnote_for` is found when the IR draws it between the matched output
  blocks. For a figure target, the caption must be linked to *a* figure: figures
  have no words, so which figure is not checked;
- **words**: the Markdown's words must be the same multiset before and after.
  They are, on every page in faithful mode.

**Set S** (12 documents, 20 pages) was used for development. **Set T** (14 single
pages, frozen 2026-11-10) was held out: scored once per pass after its rules
were frozen. Two exceptions are stated below. Set T has no cross-page relations
or footnotes, so for 3.3, 3.5 and 3.6 the held-out evidence is an audit of six
whole documents, converted with the image's path (`whole_ir.py`): V16N3 and
V20N4 (born-digital, 108 and 371 pages), V4N1 and V5N3 (scans, 36 and 27), the
1997 patent and the WO publication.

## Results

Baseline is the image's path today (`none`). Pooled R/P for IR types.

| set | passes | blk_f | typ_f | merge | hd_p | lvl | htree | caption R/P | furniture R/P | footnote R/P | caption_of | continues_from | continues_table | footnote_for |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S | none | 0.761 | 0.662 | 0.151 | 0.792 | 0.344 | 0.496 | 0.28/1.00 | 0.52/1.00 | 0.00/– | 0/32 | 0/3 | 0/3 | 0/5 |
| S | headings | | | | | **0.667** | **0.624** | | | | | | | |
| S | furniture | | | | | | | | **0.68/0.89** | | | | | |
| S | continuation | 0.762 | 0.663 | | | | | | | | | **3/3 of 3** | | |
| S | captions | | 0.666 | | 0.804 | | 0.509 | **0.47/1.00** | | | **12/32 of 13** | | | |
| S | footnotes | **0.798** | 0.704 | 0.109 | | | | | | **1.00/1.00** | | | | **5/5 of 5** |
| S | tables | | | | | | | | | | | | **1/3 of 1** | |
| S | all | **0.799** | **0.708** | **0.109** | 0.804 | 0.667 | 0.636 | 0.47/1.00 | 0.68/0.89 | 1.00/1.00 | 12/32 of 13 | 3/3 of 3 | 1/3 of 1 | 5/5 of 5 |
| T | none | 0.830 | 0.765 | 0.051 | 0.950 | 0.530 | 0.592 | 0.38/0.83 | 0.38/0.75 | – | 0/13 | – | – | – |
| T | headings | | | | | **0.783** | **0.640** | | | | | | | |
| T | furniture | | | | | | | | 0.38/0.86 † | | | | | |
| T | continuation | 0.831 ‡ | 0.766 | | | | | | | | | – | | |
| T | captions | | 0.769 | | 0.964 | | 0.600 | **0.62/0.89** | | | **7/13 of 9** | | | |
| T | all | 0.831 | 0.770 | 0.051 | 0.964 | **0.850** | **0.653** | 0.62/0.89 | 0.38/0.86 | – | 7/13 of 9 | – | – | – |

Blank cells: unchanged from `none`. "a/b of c": found / gold, of c drawn.

† Not held out. T's furniture listing was looked at while the margin rule
(furniture typed in the middle of a page goes back to text) was written. The
rule's effect on T is T12 alone.
‡ First held-out run 0.833. That run had one right join (T06) and one wrong
one: T14's margin line number "30" was joined to a paragraph, and T14's order
went from 0.00 to 0.20. The rule "a block of fewer than three words is not
prose" was added afterwards, so T is not held out for it. With it, 0.831.

### Per item

**3.1 headings.** The size step was the one dial. On S, steps from 0.2 to 0.3
form a plateau (lvl 0.72/0.67/0.68, htree 0.59/0.62/0.62). 0.25 was frozen,
being the middle of the plateau rather than its peak. On held-out T, five pages
improve and two get worse: T01, where a mixed-case section heading ranks below
its capitalised siblings, and T09. The outline signal never fired: the corpus's
bookmarks are navigation labels ("Abstract", "Claims", "Database Search
Details") and the printed headings differ ("We Claim:"). It is unit-tested only.
Contents-page nesting was not built: no gold document has a nested contents page
to check it against.

**3.2 furniture.** Most gold page numbers the pass misses on scans were never
read by Tesseract at all (S03 p12, S04, T05–T07, T10), so no IR pass can recover
them. A lone running-head rule on scans was tried and dropped: it made two
false heads on S (V2 p11's "Variety: 'Victoria' Application No, 88/025" and
V4 p27's "APPENDIX 2", both at the top of a column). Whole documents:

| document | running heads (repeat) | page numbers | lone heads |
|---|---|---|---|
| V16N3 (108 pp) | 101 | 93 | – |
| V20N4 (371 pp) | 322 | 370 | 0 (3 before the section-title rule: "APPENDIX 6", ...) |
| WO 2006/070397 (21 pp) | 18 | – | – |

Every sampled head and number was right.

**3.3 continuation.** Two early failures on S were fixed during development:

- S04 joined a photo credit ending `applicant}` to the next column. Fix: a
  closing bracket ends a block.
- S01 took `sa`, scanner noise at the foot of the page, as a paragraph's end.
  Fix: elements in the page's outer 6% are ignored. 6% rather than the
  furniture's 10%, because S07's continued paragraph ends inside 10%.

Whole-document audit (relations sampled at random, seed 7):

| document | column joins | page joins | sampled | right |
|---|---|---|---|---|
| V16N3 | 20 | 13 | 12 column, 12 page | 12/12, 11/12 + 1 plausible |
| V4N1 | 6 | 3 | all 9 | 9/9 |
| V5N3 | 1 | 3 | – | – |

**3.4 captions and figures.** Ten of S's 20 missed links are S10's staff photos.
The per-page IR has no figures for them, because PyMuPDF4LLM writes no images.
The bundle adds them from the PDF's placed images; S10 has 11 such images. Figure
crops were checked by eye: V2 p11's carnation, V16 p3's portraits. In a V4N1
audit, 12 of 12 sampled caption links were right. Bundles are byte-identical
across runs.

**3.5 footnotes.** S11 is the only footnote page in the gold sets, and the only
notes block in V16N3. It is found whole: 5 notes, 69 markers written as `[^n]`.
Scans are refused (see ADR-053).

**3.6 continued tables.** Of S's three continuations, only S01's panel reaches
the IR as two tables. The first rule linked tables of equal width, which on V5N3
linked two pairs of different varieties' tables side by side. The header rule
replaced it. After it: V5N3 has 5 page continuations and 1 panel, V4N1 2 index
panels, all right on inspection. No relation is drawn on T.

## Byte-neutrality

With `WISEAU_STRUCTURE` unset, the default path's Markdown must be unchanged.
The serializer now skips empty elements instead of joining them, which
`clean_markdown` made invisible before. The layout pass now types picture
regions as figures, keeping their text. The gate (`whole_docs.py`, all 15 test
PDFs, old checkout vs new) and an image-path check (V4N1 and WO with tables and
layout on) are recorded in STATUS.md with their result.

## Not done

- Contents-page nesting for 3.1.
- Footnotes on scans.
- Merging continued tables into one Markdown table.
- A figure's identity in scoring.
- The bundle in the frontend and on the MCP surface.
- Whether to turn passes on in the Docker image. That is the user's call, as
  for ADR-047/048. No pass needs a model or a dependency. All six take about
  60 ms on V16N3 (108 pages) and on V20N4 (371 pages).
