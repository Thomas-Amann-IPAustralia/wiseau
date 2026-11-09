# Text-layer audit: should we trust a scan's hidden OCR? (2026-11-08)

Roadmap 14 item 0.2, and the user's request to check pages that someone else
(Adobe Acrobat) had already OCR'd for errors, because "the more I learn about
OCR the less I trust anyone/anything besides our process". No paid APIs.
Decision: [ADR-040](../decisions.md).

**Answer: no.** On a random sample of Acrobat-OCR'd PBR pages, the hidden layer
had lost whole tables, and the default path, which used to pass it through, got
a third of the numbers wrong or missing. Our own OCR of the same pixels is far
better. The default now OCRs such pages.

## The material

Seven test PDFs are scans with an invisible OCR text layer (every character in
PDF render mode 3, under a full-page image), 225 pages in all:

| PDF | Pages | Layer producer |
|---|---|---|
| Vol 1 No 3, Vol 2 No 2 (PBR journals) | 20 + 40 | Adobe Acrobat 8.13 Paper Capture Plug-in |
| Five patent specifications, 1963–2000 | 145 | none recorded |

Before ADR-040 the default path saw 16+ characters of text on these pages and
read the layer as the page. It never looked at the pixels.

## Three sets of blind references

All transcribed by hand from the page images **before** any layer or engine
output was compared with them, then committed (`8247cc8`, and set F's `1be3ded`
earlier). Conventions as for set F: printed punctuation kept (‘ ’ “ ” – —),
printed typos kept, text inside photographs left out.

- **Set L, the audit (new).** A plain seeded random draw (seed 20261110,
  `survey.py --per-doc 4`, near-empty pages excluded) of 4 pages from Vol 1 No 3
  and 4 from Vol 2 No 2, set F excluded. Not stratified, because its job is an
  unbiased estimate of how wrong the layer is. It drew V1N3 p7, p12, p16, p19 and
  V2N2 p23, p29, p30, p33 (3,632 words). This is the development set for the rule.
- **Set F's 8 Vol 2 No 2 pages** (frozen 2026-11-08, earlier session): held out.
- **Set E (new, transcribed this session):** two pages from each patent PDF and
  from the image-only WO publication (12 pages, 3,947 words): do-no-harm. Typed
  margin line numbers (5, 10, 15 …) are kept as text; handwritten margin numbers
  (P63a p7) and chemical structure drawings (P97 p13, p15) are left out. No drawn
  page duplicates another scan in its PDF (image similarity ≤ 0.70).

## Engines

`run_engines.py` gained three: `hidden-layer` (the layer as is, `page.get_text()`,
no Markdown), `forced-ocr` (the default path with `WISEAU_OCR_MODE=force`), and
`trust-layer` (the default before ADR-040). `word_errors.py` lists each misread,
lost and inserted word against the references.

## Results

Cells are wer / word recall / number recall.

### Set L: random Acrobat-OCR'd PBR pages (development)

| Page | old default (trust-layer) | layer as is | new default (ADR-040) |
|---|---|---|---|
| V1N3_p7 | 0.094 / 0.982 / 0.792 | 0.020 / 0.982 / 0.792 | 0.067 / 0.984 / 0.958 |
| V1N3_p12 | 0.032 / 0.980 / 1.000 | 0.016 / 0.988 / 1.000 | 0.012 / 0.992 / 0.909 |
| V1N3_p16 | 0.040 / 0.960 / 0.667 | 0.040 / 0.960 / 0.667 | 0.022 / 0.978 / 1.000 |
| V1N3_p19 | 0.352 / 0.972 / 0.929 | 0.085 / 0.972 / 0.929 | 0.090 / 1.000 / 1.000 |
| V2N2_p23 | 0.599 / 0.408 / 0.039 | 0.599 / 0.408 / 0.039 | 0.053 / 0.947 / 0.930 |
| V2N2_p29 | 0.503 / 0.497 / 0.043 | 0.499 / 0.501 / 0.043 | 0.014 / 0.986 / 0.983 |
| V2N2_p30 | 0.365 / 0.946 / 0.925 | 0.380 / 0.946 / 0.925 | 0.073 / 0.934 / 0.932 |
| V2N2_p33 | 0.972 / 0.982 / 0.933 | 0.128 / 0.989 / 0.967 | 0.004 / 0.996 / 0.967 |

| | wer | word recall | number recall | punct. recall | s/page |
|---|---|---|---|---|---|
| old default | 0.370 | 0.841 | 0.666 | 0.661 | 0 |
| new default | **0.042** | **0.977** | **0.960** | **0.927** | 7 |

What the Acrobat layer got wrong (`word_errors.py hidden-layer --set l`):

- **Whole tables missing.** V2N2 p23 (guinea grass comparison) and p29 (rose
  comparison) hold only the prose: the layer has 4% of each page's numbers. On
  V1N3 p19 the fee column (300, 1000, 50, 55 …) is gone, and on V2N2 p30 most of
  the table's figures. In all, 21% of the sample's words were not in the layer.
- **Characters:** `1` → `l`, `Li` → `U` (`Lilium` → `ulium`, `Linum` → `unum`,
  `Limonium` → `umonium`), `Ilex` → `liex`, `St` → `5t`, `Solander` → `50iander`,
  `Wyalong` → `Wvalong`, `cardiophylla` → `cardiophyjla` (all 7 times),
  `aftermath` → `ahermath`, `Gatton` → `G tton`.
- **Spacing:** `154lines`, `April88`, `AlliumCepa`, `BoxW252`; `th rough`,
  `poll ination`, `com parison`.
- **Punctuation:** printed curly quotes typed as straight ones throughout.
- **Order, made worse by the old default:** PyMuPDF4LLM read V2N2 p33's layer
  right column first (wer 0.972 although 98% of the words were there) and
  scrambled V1N3 p19 (0.352, against the raw layer's 0.085).

Where our OCR erred on set L it was small: 2.6% of words misread, 0.9% lost
(the layer: 2.5% misread, 21.2% lost).

### Set F's Vol 2 No 2 pages (held out)

| Page | old default (trust-layer) | layer as is | new default (ADR-040) |
|---|---|---|---|
| V2N2_p3 | 0.040 / 0.964 / 0.400 | 0.044 / 0.964 / 0.400 | 0.040 / 0.988 / 0.933 |
| V2N2_p4 | 0.015 / 0.993 / 1.000 | 0.015 / 0.993 / 1.000 | 0.003 / 0.997 / 0.955 |
| V2N2_p6 | 0.018 / 0.985 / 0.987 | 0.018 / 0.985 / 0.987 | 0.008 / 0.992 / 0.975 |
| V2N2_p10 | 0.140 / 0.872 / 0.888 | 0.140 / 0.872 / 0.888 | 0.099 / 0.901 / 0.910 |
| V2N2_p12 | 0.086 / 0.914 / 0.920 | 0.086 / 0.914 / 0.920 | 0.082 / 0.922 / 0.912 |
| V2N2_p16 | 0.100 / 0.903 / 0.933 | 0.100 / 0.903 / 0.933 | 0.090 / 0.910 / 0.922 |
| V2N2_p31 | 0.004 / 0.998 / 1.000 | 0.030 / 0.995 / 1.000 | 0.005 / 0.998 / 1.000 |
| V2N2_p32 | 0.051 / 0.952 / 0.953 | 0.051 / 0.952 / 0.953 | 0.035 / 0.969 / 0.971 |

wer 0.057 → 0.045, word recall 0.948 → 0.960, number recall 0.885 → 0.947. Better
on 6 pages, equal on one, and V2N2 p31 0.004 → 0.005. These layers were complete
(set F's draw happened to miss the coverage failures), so this measures their
*accuracy*: 4.2% of words misread by the layer against 3.1% by our OCR. V2N2 p3's
layer has 40% of its numbers right; ours 93%.

All 14 set-F pages under the new default (the held-out PBR baseline for Tier 1):
wer 0.046, cer 0.014, ord 0.075, word recall 0.966, number recall 0.947.

### Set E: patents and WO (do-no-harm)

| Page | old default (trust-layer) | layer as is | new default (ADR-040) |
|---|---|---|---|
| P63a_p3 | 0.167 / 0.886 / 0.667 | 0.167 / 0.886 / 0.667 | 0.132 / 0.897 / 0.667 |
| P63a_p7 | 0.086 / 0.922 / 0.800 | 0.081 / 0.927 / 0.800 | 0.145 / 0.868 / 0.600 |
| P63b_p14 | 0.101 / 0.917 / 0.600 | 0.101 / 0.917 / 0.600 | 0.092 / 0.943 / 0.714 |
| P63b_p20 | 0.030 / 0.970 / 0.625 | 0.030 / 0.970 / 0.625 | 0.054 / 0.970 / 0.750 |
| P85_p6 | 0.014 / 0.986 / 0.667 | 0.014 / 0.986 / 0.667 | 0.110 / 0.979 / 0.667 |
| P85_p21 | 0.049 / 0.963 / 0.562 | 0.045 / 0.963 / 0.562 | 0.086 / 0.930 / 0.500 |
| P97_p13 | 0.196 / 0.913 / 0.819 | 0.176 / 0.924 / 0.826 | 0.084 / 0.959 / 0.899 |
| P97_p15 | 0.223 / 0.935 / 0.908 | 0.210 / 0.938 / 0.901 | 0.114 / 0.961 / 0.937 |
| P00_p4 | 0.020 / 0.980 / 0.333 | 0.020 / 0.980 / 0.333 | 0.065 / 0.990 / 0.667 |
| P00_p15 | 0.050 / 0.953 / 0.792 | 0.050 / 0.953 / 0.792 | 0.038 / 0.994 / 0.917 |
| WO06_p4 | 0.032 / 0.989 / 0.792 | 1.000 / 0.000 / 0.000 | 0.032 / 0.989 / 0.792 |
| WO06_p16 | 0.059 / 0.976 / 0.886 | 1.000 / 0.000 / 0.000 | 0.059 / 0.976 / 0.886 |


Patent pages only: wer 0.094 → 0.092, word recall 0.942 → 0.949, number recall
0.677 → 0.732: neutral on average, but **5 of 10 pages are worse.** The patents'
layer engine (unknown) is a good one on these documents:

- On the 1963 and 1985 typewritten specifications, Tesseract confuses `e`, `o`,
  `c` and `a` (`ketenes`, `contrel`, `centaining`, `fcr`), where the layer
  reads them right more often.
- Tesseract reads the black scanner bands at the top and bottom of the P85 scans
  as junk words (`t snes ee nsn bago …`): P85 p6 0.014 → 0.110.
- Our OCR wins on the 1997–2000 typeset pages (P97 p13 0.196 → 0.084) and keeps
  the typed margin line numbers the layer dropped.

These are recogniser and preprocessing weaknesses (roadmap 1.2: border cleanup;
1.3: recogniser bake-off), not evidence the layer can be trusted: nothing on the
page says which pages are the good ones (below).

The WO pages have no text layer and are unchanged (wer 0.045).

## Why always OCR, not a gate

The roadmap proposed re-OCR only when the layer "disagrees with fresh OCR or
looks wrong". Measured on all 26 layer pages:

- **Coverage** (layer words ÷ OCR words) is 0.43 and 0.50 on the two pages that
  lost their tables and 0.93–1.03 everywhere else. It catches those two and
  nothing more: V2N2 p30, p33 and V1N3 p16, which our OCR also wins, sit at 0.99–1.01.
- **Agreement** between the two texts (bag-of-words overlap 0.83 on both P63a p3,
  where OCR wins, and P63a p7, where the layer wins) does not separate the cases.
- Any such check costs a full OCR pass, so a gate saves no time.

Always OCR-ing these pages is simpler, is our own process end to end, and on the
evidence is better on PBR and neutral on patents.

## Checks

- The rule fires on exactly the 225 layer pages across all 16 test PDFs, and on
  no born-digital or image-only page. Sets A–D contain no layer pages, so they
  are unchanged.
- `trust-layer` reproduces the old default byte for byte on all 34 set E/F/L
  pages; the new default equals `forced-ocr` byte for byte on them.
- Tests: 519 passed, 8 skipped (6 new; the two that exercise the rule fail with
  `OCR_INVISIBLE_LAYER = False`).

## Not done

- No reference was adjudicated against engine output yet (the set B/C/D
  practice). The layer's and our OCR's disagreements are the natural list.
- Pages are 5–7 s slower each. A 145-page patent bundle takes ~15 minutes on the
  default path.
- `WISEAU_OCR_MODE=off` still returns the layer, by design.
