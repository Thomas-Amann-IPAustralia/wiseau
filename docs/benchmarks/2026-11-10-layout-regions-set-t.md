# Layout regions on held-out pages: set T (2026-11-10)

The follow-up to [`2026-11-10-layout-regions.md`](2026-11-10-layout-regions.md).
That test was scored on set S, which was then looked at while the adapter was
built. The user asked for the result to be tested on other pages before
deciding how to deploy it. **No production change**, and no paid API.

## What was done, in order (each step committed before the next)

1. **Draw** (`13061d7`, `survey/set-t/draw.py`, seed 20261110), 14 single
   scanned pages:
   - 10 PBR journal pages, stratified by layout group (3 two-column prose,
     3 two-column + data, 2 tables, 2 single column);
   - one page from each of the four non-PBR publications (1963 and 1985
     typewritten patents, a 2000 typeset patent, a 2006 WO publication).

   It leaves out set S's pages, set A's (non-blind references), and V2 p9
   (inspected in the smoke test).
2. **Blind structural gold** (`fec2071`,
   [`benchmarks/bakeoff/structure-t/`](../../benchmarks/bakeoff/structure-t/README.md)).
   Each page was annotated from its image onto the frozen text reference:
   306 blocks, 24 tables. Words were checked equal to the references, and the
   self-check was perfect on 13 of 14 (T11's page number reads as a list item,
   as S05's does).
3. **Frozen code** (`4ca2853`). It holds:
   - the as-tested adapter, unchanged (its set S outputs rebuild byte for byte);
   - a **v2** variant with the two fixes set S suggested, declared before set T
     was run: ADR-038 stitched tables kept whole, and `vision_footnote` →
     caption;
   - a fix to the scorer: a shadowed variable made every engine after the first
     look up the wrong gold set. Set S's numbers reproduce exactly.
4. **Models run on set T**, then **scored once** (the detector in `6125d7f`,
   OvisOCR3 after it).
   - OvisOCR3: 14/14 pages valid JSON, median 146 s/page, 5.2 GB.
   - Detector: about 1 s/page.

## Results on set T (14 documents, means; IR types pooled)

| engine | blk_f | typ_f | split | merge | ord | hd_p | hd_r | htree | teds | cap R/P | furn R/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| default (`pymupdf`) | 0.649 | 0.503 | 0.115 | 0.244 | 0.007 | – | 0.000 | 0.000 | 0.291 | 0.00/– | 0.00/– |
| image default (`tables-tatr`) | 0.668 | 0.537 | 0.113 | 0.238 | 0.007 | – | 0.000 | 0.000 | 0.681 | 0.00/– | 0.00/– |
| PP-DocLayoutV3 relabel + TATR | 0.668 | 0.597 | 0.113 | 0.238 | 0.007 | 0.708 | 0.380 | 0.348 | 0.681 | 0.38/1.00 | 0.31/0.62 |
| **PP-DocLayoutV3 regroup + TATR** | **0.830** | **0.765** | **0.039** | **0.051** | **0.003** | **0.950** | **0.781** | **0.592** | 0.681 | 0.31/1.00 | 0.38/0.75 |
| PP-DocLayoutV3 regroup + TATR, v2 | 0.830 | 0.765 | 0.039 | 0.051 | 0.003 | 0.950 | 0.781 | 0.592 | 0.681 | 0.38/0.83 | 0.38/0.75 |
| OvisOCR3 regroup + TATR | **0.836** | **0.794** | 0.045 | **0.034** | **0.000** | 0.901 | **0.926** | **0.760** | 0.681 | **0.62/1.00** | 0.38/0.86 |
| OvisOCR3 regroup + TATR, v2 | 0.836 | 0.794 | 0.045 | 0.034 | 0.000 | 0.901 | 0.926 | 0.760 | 0.681 | 0.69/0.90 | 0.38/0.86 |

By group (regroup + TATR against `tables-tatr`):

| group | blk_f | typ_f | merge | ord | hd_r |
|---|---|---|---|---|---|
| PBR journals (T01–T10) | 0.789 → **0.913** | 0.662 → **0.846** | 0.200 → **0.071** | 0.010 → 0.004 | 0.000 → **0.848** |
| other publications (T11–T14) | 0.366 → **0.622** | 0.226 → **0.563** | 0.333 → **0.000** | 0.000 → 0.000 | 0.000 → **0.556** |

Per document, block F1 rises on 10 of 14 and is level on two. It falls on two:
T10 (an editorial page with a photo, signature blocks and a contact table,
0.80 → 0.76) and T14 (the WO page, 0.56 → 0.53). Reading order slips on one
page (T01, 0.00 → 0.04) and improves on another (T07, 0.10 → 0.00). No
document's tables change (TEDS identical on all 10 with tables), and no word
changes anywhere (checked on every page at build).

## What it says

1. **The set S result holds on unseen pages, and is larger.** Regrouping by
   PP-DocLayoutV3 over the shipping path:
   - lifts block F1 by 0.16 and typed-block F1 by 0.23;
   - cuts merged blocks from 24% to 5%;
   - finds 78% of headings, 95% of its headings being real (from none);
   - leaves tables and words untouched, and does not hurt reading order.

   The S06-style failure (a list read column by column) did not occur here.
2. **It generalises beyond the PBR journal.** On the four patent/WO pages the
   default path's blocks are poor (F1 0.37). The pass nearly doubles them
   (0.62), finds the titles and section headings (recall 0.56), and removes
   every merge. These pages were never seen by anything in this work.
3. **v2 changes nothing but captions.** No page in set T has an ADR-038
   stitched list for the first fix to act on. The second fix trades caption
   precision for recall (0.31/1.00 → 0.38/0.83). Neither is worth a separate
   decision; the photo-credit mapping is a call for the ADR.
4. **Relabelling alone is not enough.** It gets heading types (hd_p 0.71) but
   none of the segmentation (block F1 unchanged). The gain comes from using the
   regions as block boundaries.
5. **What's still missing:** heading levels (every `paragraph_title` is level
   2, so `htree` is 0.59, not higher), captions (recall ≤ 0.38), and page
   furniture precision (0.75: some running text is called a header or footer).

6. **On these pages OvisOCR3 labels better than the detector, which set S
   did not show.** Its blocks are the same (F1 0.836 against 0.830). It finds
   more headings (0.93 against 0.78) and builds a better heading tree (0.76
   against 0.59). It finds twice the captions (8 against 4 of 13), and its
   reading order is perfect on every page.
   - **In counts, the edge is modest.** Pooled over sets S and T
     (32 documents), OvisOCR3 finds 147 of 170 gold headings against the
     detector's 139, and 14 of 45 captions against 8.
   - **It also marks more false headings:** 175 heading outputs against 155.
     Its heading precision on the PBR pages is 0.87 against 0.94.
   - **Most of the gap on set T is the four patent/WO pages**, which have few
     headings (heading recall 1.00 against 0.56 there). On the PBR pages the
     two are close: heading recall 0.90 against 0.85, block F1 0.903 against
     0.913.
   - **It is worse on T10** (block F1 0.61 against 0.76).
   - **Cost:** still about 180× the detector's on CPU (146 s against 0.8 s a
     page).

   So the generative model is a modest, real improvement in labelling, not
   in segmentation. That reopens the GPU question: a T4 (the prepared Kaggle
   leg) would show whether it can run in seconds a page.

## For the deployment decision

Two candidates, the same pass with a different region source, on OCR'd
pages only:
- **PP-DocLayoutV3:** CPU, about 1 s/page, almost all of the gain. It is the
  one that fits the current CPU Space.
- **OvisOCR3:** a little better on headings and captions. Usable only with a
  GPU (to be measured on the T4), or as a slow opt-in.

For PP-DocLayoutV3:
- **Cost:** ~0.8 s and ~1.6 GB peak per scanned page on 4 vCPU. The model is
  128 MB safetensors at a pinned revision. It is deterministic: identical
  output on re-run.
- **A dependency conflict to resolve first.** PP-DocLayoutV3 exists only in
  transformers 5.x: it is absent from 4.57.6 (checked in the wheel).
  `requirements-tables.txt` pins TATR to 4.57.6, because "5.x rejects the
  Table Transformer checkpoints' config". 5.22.1 still has a
  `table_transformer` module, so the first thing to try is loading TATR's
  pinned checkpoints on 5.22.1. The fallbacks are an ONNX export of the
  detector, or a separate process.
- **Steps before it ships:**
  - an ADR;
  - the serializer change (writing `##` for typed headings changes the output,
    so ADR-043's whole-document gate, `whole_docs.py`);
  - the transformers conflict above;
  - deciding opt-in vs default.
