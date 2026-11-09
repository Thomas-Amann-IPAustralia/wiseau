# Preprocessing ablation: none of four variants beats the original pixels (2026-11-09)

Roadmap 14 item 1.2 (the border part shipped as ADR-041). No paid APIs. No
behaviour change: the default path keeps OCR-ing the page as scanned.

## Method

`benchmarks/bakeoff/preprocess.py` renders each scanned page at 300 dpi, changes the
pixels with OpenCV, and builds a one-page PDF holding only the new image, placed so
that rendering it at 300 dpi gives exactly those pixels back. The default path
(ADR-038/040/041/042) then converts that page. Born-digital pages are left alone.

Re-rendering, not editing the embedded image, because most PBR scans are several
image strips and the scans mix 1-bit JBIG2/CCITT (48 of the 72 scored scans), greyscale
or colour JPEG/JPX (5), and bilevel text strips with greyscale photographs (19). The
round trip is pixel-exact at 300 dpi; the control variant **`ident`** does it and
changes nothing, and it reproduced the default's scores on all 72 scored scans
(every `Δ` below is against `ident`, so it is the processing alone).

| Variant | What |
|---|---|
| `deskew` | Rotate by the projection-profile skew angle (±3°, 0.05° steps, 75 dpi ink map) when it is ≥ 0.1° |
| `norm` | `deskew`, then divide by a 41 px closing (background flattening) and stretch the 1st–99th percentiles |
| `sauvola` | Sauvola threshold, 31 px window, k 0.2, R 128 |
| `denoise` | Non-local means (h 10), then remove dark specks ≤ 4 px |

Every page's features are in `benchmarks/bakeoff/survey/preprocess-features.json`:
scan type, native dpi, skew, contrast (1st–99th percentile spread), blur (variance of
the Laplacian) and specks per megapixel. **Skew is small everywhere**: |skew| ≤ 0.75°
(P85 p6), ≤ 0.3° on 70 of 72 pages.

Development sets: A (scans), B, C, D, L. Held out: E (other publications) and F
(held-out PBR). Runs on a 4-vCPU box with other jobs beside them, so seconds are
only comparable within this table.

## Results (72 scored scans)

| Variant | wer | Δwer | Δcer | better / worse / same | ord | s/page |
|---|---|---|---|---|---|---|
| original (`pymupdf`) | 0.056 | — | — | — | 0.088 | 6.8 (quiet box) |
| `ident` (control) | 0.056 | 0 | 0 | 0 / 0 / 72 | 0.088 | 8.2 |
| `deskew` | 0.072 | +0.016 | +0.013 | 19 / 18 / 35 | 0.100 | 8.3 |
| `norm` | 0.097 | +0.041 | +0.034 | 16 / 31 / 25 | 0.120 | 8.5 |
| `sauvola` | 0.107 | +0.051 | +0.030 | 15 / 39 / 18 | 0.106 | 8.7 |
| `denoise` | 0.059 | +0.003 | −0.000 | 17 / 31 / 24 | 0.087 | 10.8 |

By scan type (Δwer against `ident`):

| Variant | bilevel (48) | bilevel + photos (19) | grey/colour (5) |
|---|---|---|---|
| `deskew` | +0.026 | −0.005 | −0.004 |
| `norm` | +0.031 | +0.080 | −0.003 |
| `sauvola` | +0.006 | +0.115 | +0.243 |
| `denoise` | +0.002 | +0.006 | −0.005 |

Held out only (E + F, 26 pages): `deskew` +0.027, `norm` +0.029, `sauvola`
+0.006, `denoise` +0.002. On set E's patents alone every variant is within ±0.002.

## Why they lose

- **Rotation breaks table layout.** The large deskew losses are reading order, not
  reading: V4N1 p27 (skew 0.30°) wer 0.018 → 0.782 with word recall still 0.903,
  `ord` 0.085 → 0.646; V5N3 p19 (0.10°) 0.121 → 0.432; V5N1 p18 (0.10°) 0.299 →
  0.481. A tenth of a degree is enough to change how Tesseract's psm 3 cuts a table
  into blocks, and ADR-038's row stitching depends on those blocks. With skews this
  small there is nothing for deskew to win.
- **Binarising hides photographs from the photo mask.** ADR-037 finds photos by
  their mid-grey tones. Sauvola and the normalisation's contrast stretch push those
  to black and white, so photo noise comes back: bilevel-plus-photo pages lose
  +0.115 (Sauvola) and +0.080 (norm).
- **Sauvola on low-resolution greyscale** (V2N2's 150 dpi JPX pages, V1N3's JPEG
  pages) breaks thin strokes: grey/colour +0.243, set L +0.271.
- **Denoise does little either way.** Bilevel scans are already clean after
  JBIG2/CCITT; removing 4 px specks also removes some full stops and i-dots.

## Per-page rules

Searched on the development pages only: each variant applied only where one feature
passes a threshold (skew, specks, contrast, blur, native dpi, grey/colour, photos),
every threshold at a page's value. The best rule (`deskew` where blur ≤ 1407)
improves the development mean by 0.0017; on the held-out pages it improves the mean
by 0.0012 but makes 4 pages worse for 1 better, and the next threshold up costs
+0.028. The grey/colour pages lean the right way for `denoise`, `norm` and `deskew`
(Δ −0.003 to −0.005), but that is 5 pages and differences under 0.01. **No rule
ships; the original pixels stay the default.** Script:
`benchmarks/bakeoff/rule_search.py`; re-run it if a new set arrives.

## What this does and doesn't say

- It says that on this corpus (scans of printed pages, small skew, clean bilevel images)
  generic image cleanup costs more than it gains, mostly through layout and the
  photo mask, and that the one cleanup that worked (ADR-041) was targeted at one
  artefact.
- It doesn't rule out a variant tuned for one failure (for example a rotation only
  above 1°, which no test page has), or preprocessing for a different recogniser:
  the variants were scored through Tesseract only.
