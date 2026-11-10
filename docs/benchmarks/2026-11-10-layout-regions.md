# Layout regions for scans: OvisOCR3 vs PP-DocLayoutV3, CPU leg (2026-11-10)

Roadmap 14 item 3.7b, the spike proposed in
[`assessment-2026-11-09-ovisocr3-teleocr.md`](../assessment-2026-11-09-ovisocr3-teleocr.md).
**No production change.** Nothing in `backend/` was touched. No paid API was
used. The Kaggle GPU leg is prepared (`benchmarks/bakeoff/kaggle/`) but not
yet run.

## Question

Can a layout model's regions add structure that the default path lacks
(headings, block boundaries, captions, page furniture) to Wiseau's scans?
The model contributes **no text**. Is a 0.8B generative model (OvisOCR3) worth
its cost over a CPU detector with the same label set (PP-DocLayoutV3)?

## Method

- **Models.**
  - **OvisOCR3** `17f255e2`: its own `document_layout` prompt, through
    Transformers 5.22.1 (`Qwen3_5ForConditionalGeneration`; the card documents
    only vLLM). FP32, greedy, 4 threads, page rendered at 200 dpi
    (1653×2339 px, 3,978 input tokens), `max_new_tokens` 4096.
  - **PP-DocLayoutV3** `e4f489dc` (GLM-OCR's pinned layout stage): threshold
    0.5, its own reading order.
  - Both run with torch 2.15.0 CPU on a 4-vCPU Xeon 2.1 GHz, one page at a
    time, nothing else running. One exception: about 20 s of a scoring run
    overlapped the first smoke-test page, which is not in the table.
  - Runner: `layout_models.py`. Raw output, parsed regions, settings and
    versions are kept in `outputs/<model>/<page>.regions.json`.
- **Sidecar adapter** (`layout_regions.py`). It starts from the default path's
  committed IR (`outputs/pymupdf/` or, as the Docker image ships,
  `outputs/tables-tatr/`) and touches only OCR'd pages. Each line goes to the
  region holding ≥ 50% of its area.
  - **relabel:** an element whose lines lie ≥ 50% inside one region takes that
    region's type. Segmentation is unchanged.
  - **regroup:** each region becomes one element, in the model's order. Lines
    outside any region stay where they were. TATR tables move as whole units.
  - Category map: `paragraph_title` → `## heading`, `doc_title` → `# title`,
    `figure_title` → caption, `header`/`footer`/`number` → page furniture, the
    rest → text.
  - **Every word is Tesseract's.** The build aborts if a page's words differ
    from its base as a multiset. That check caught one bug during the run: the
    remainder of an ADR-038 stitched table re-emitted its rows. It was fixed
    before any variant was read.
- **Scoring.** `structure.py`'s metrics on set S (12 documents; S10–S12 are
  born-digital and unchanged by design), plus IR-type recall and precision for
  the classes Markdown can't write.
  - The OCR-path settings were fixed from the categories' meaning before any
    score was seen. Nothing was tuned on set S, which holds held-out pages from
    D, E and F.
  - The label metric's definition was corrected once, after the first table:
    it had counted recall over matched blocks only. It now counts every gold
    block, as `hd_r` does. That made it stricter, not more favourable.

## Results

**Cost** (20 set S pages, 4 vCPU):

| | median s/page | range | peak RSS | failures |
|---|---:|---:|---:|---:|
| PP-DocLayoutV3 | **0.82** | 0.75–0.96 | 1.6 GB | 0 |
| OvisOCR3 layout-only, FP32 | **150** | 59–305 | 5.2 GB | 0 (all valid JSON, all `stop`) |

OvisOCR3's time follows its output length, about 0.13 s per generated token
on top of prefill. The median page emits 1,070 tokens, much of it the JSON's
pretty-printing whitespace. Both models gave identical output when run again
on P63a p7.

**Structure, set S, 12 documents** (means; `hd R/P` etc. are IR types pooled
over blocks):

| engine | blk_f | typ_f | merge | ord | hd_p | hd_r | htree | teds | hd R/P | cap R/P | furn R/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| default (`pymupdf`) | 0.624 | 0.456 | 0.335 | 0.029 | 0.375 | 0.100 | 0.075 | 0.279 | 0.03/0.60 | 0.00/– | 0.00/– |
| image default (`tables-tatr`) | 0.649 | 0.498 | 0.332 | 0.029 | 0.375 | 0.100 | 0.075 | 0.572 | 0.03/0.60 | 0.00/– | 0.00/– |
| PP-DocLayoutV3 relabel + TATR | 0.649 | 0.547 | 0.332 | 0.029 | 0.650 | 0.357 | 0.249 | 0.572 | 0.41/0.83 | 0.12/1.00 | 0.48/0.92 |
| OvisOCR3 relabel + TATR | 0.649 | 0.531 | 0.332 | 0.029 | 0.551 | 0.363 | 0.257 | 0.572 | 0.42/0.70 | 0.22/0.88 | 0.44/0.92 |
| **PP-DocLayoutV3 regroup + TATR** | **0.766** | **0.670** | **0.138** | 0.041 | **0.787** | 0.745 | **0.500** | 0.571 | 0.82/0.88 | 0.12/1.00 | 0.52/1.00 |
| OvisOCR3 regroup + TATR | 0.747 | 0.639 | 0.157 | 0.031 | 0.764 | **0.772** | **0.500** | 0.565 | 0.84/0.82 | 0.19/1.00 | 0.48/1.00 |

Against docling and GLM-OCR, on the 8 documents every engine has:

| engine | blk_f | typ_f | merge | ord | hd_r | hd_p | htree | teds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| docling | 0.732 | 0.667 | 0.131 | 0.015 | **0.865** | 0.765 | 0.485 | 0.513 |
| GLM-OCR | 0.646 | 0.606 | 0.135 | 0.027 | 0.801 | **0.845** | 0.521 | **0.717** |
| `tables-tatr` | 0.756 | 0.588 | 0.244 | **0.002** | 0.000 | 0.000 | 0.000 | 0.558 |
| PP-DocLayoutV3 regroup + TATR | **0.823** | **0.731** | **0.091** | 0.024 | 0.797 | 0.781 | **0.571** | 0.556 |
| OvisOCR3 regroup + TATR | 0.798 | 0.687 | 0.107 | 0.008 | 0.835 | 0.752 | **0.571** | 0.549 |

## Findings

1. **Layout regions supply what the scans lack, without touching a word.**
   Regrouping lifts block F1 from 0.649 to 0.766. Merged blocks fall from 33%
   to 14%. Heading recall goes from 0.10 to 0.75 and the heading tree from
   0.08 to 0.50. On the shared documents it beats docling and GLM-OCR on block
   F1, and it keeps Tesseract's words, which are better on these scans (ADR-038).
   Relabelling alone gets the heading types but none of the segmentation.
2. **OvisOCR3 is no better than the detector it appears to have learned from.**
   Their boxes nearly coincide page by page. Its 24 categories are
   PP-DocLayoutV3's 25 minus `reference`. Both merge V16 p90's two directory
   columns into one table. Both split V5N1 p18's side-by-side panels correctly.
   OvisOCR3 is slightly better on heading recall (0.77 vs 0.75) and captions
   (0.19 vs 0.12), and slightly worse on blocks and types. **It costs about
   180× the time and 3× the memory.** On CPU it is not viable as an opt-in
   engine at 2½ minutes a page, and it adds nothing over the detector to
   justify a GPU.
3. **Reading order gets slightly worse** (0.029 → 0.041 with the detector, 0.031
   with OvisOCR3). The model's order is close to but not as good as ADR-035/038's
   column logic. A later version could keep the default's order and take only
   segmentation and types from the regions.
4. **Two failure modes, both on S06** (V4 p25–27): block F1 0.83 → 0.67, TEDS
   0.44 → 0.29.
   - **A header-less two-column list.** psm 3 and ADR-038 had stitched it into
     rows. The layout model calls each column a text region, so regrouping
     reads it column by column ("Application No." ×5).
   - **A directory annotated as one list** comes out as one region per entry.
   The first suggests keeping ADR-038 stitched tables as units, like TATR
   tables. It was found on set S, so it must be validated on fresh pages
   before it counts.
5. **Captions remain weak** for both (recall ≤ 0.22). Of set S's scanned gold
   captions (regroup + TATR, PP-DocLayoutV3 / OvisOCR3):
   - 4 / 6 come out as captions;
   - 7 / 4 as body text;
   - 5 / 5 as footnotes;
   - 4 / 5 unmatched.

   The footnotes are photo credits ("(Photo supplied by applicant)") that both
   models label `vision_footnote`. The category map, fixed beforehand, sends
   that to footnote, but the gold calls it a caption. Mapping
   `vision_footnote` → caption is an obvious change, but it was seen on set S,
   so it needs fresh pages too.
   **Heading levels:** every `paragraph_title` is level 2. Level recovery is
   still roadmap 3.1's job.

> **Update (set T, same day):** on 14 held-out pages
> ([`2026-11-10-layout-regions-set-t.md`](2026-11-10-layout-regions-set-t.md)),
> OvisOCR3 labels headings and captions better than the detector. Its blocks
> are the same. Over both sets: 147 vs 139 of 170 headings, and 14 vs 8 of
> 45 captions. The recommendation below to drop it is withdrawn. Its cost
> stands, so on CPU the detector is still the practical choice, and the
> Kaggle GPU leg is worth running.

## Recommendation

- **Drop OvisOCR3 (and TeleOCR) as candidates.** On the same label set they
  add nothing a 0.8 s CPU detector doesn't give. The GPU leg is now optional.
  It would only confirm T4 fp16 viability and speed, and won't change the
  decision unless it shows quality that the CPU FP32 reference didn't.
- **Take PP-DocLayoutV3 regrouping forward as a candidate** for 3.7 / 3.1,
  through the usual gates:
  - its own ADR;
  - a decision on whether a 1.6 GB, ~0.8 s/page PyTorch pass fits next to
    TATR (which already ships torch);
  - fresh held-out structure pages, since set S has now been looked at;
  - the S06 fixes (stitched tables kept whole), checked against those fresh
    pages;
  - and the serializer gate. Writing headings as `##` changes the output, so
    ADR-043 requires `whole_docs.py`.

## Reproduce

```bash
# scratch venv: CPU torch 2.15.0 + torchvision 0.29.2, transformers 5.22.1, accelerate 1.15.0,
# pymupdf 1.28.0, opencv-python-headless 5.0.0.93; HF_HOME with the two pinned snapshots
python benchmarks/bakeoff/layout_models.py pp-doclayoutv3 --set s --threads 4
python benchmarks/bakeoff/layout_models.py ovisocr3-layout --set s --threads 4      # ~50 min
cd benchmarks/bakeoff
python layout_regions.py build pp-doclayoutv3 --base tables-tatr    # and without --base, and for ovisocr3-layout
python layout_regions.py score tables-tatr pp-doclayoutv3-regroup-tatr ovisocr3-layout-regroup-tatr ...
```
