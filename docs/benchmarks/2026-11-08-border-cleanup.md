# Border cleanup: scanner bands painted out before OCR (2026-11-08)

Roadmap 14 item 1.2 (the border part of the preprocessing ablation), asked for by
the user after the text-layer audit. No paid APIs. Decision:
[ADR-041](../decisions.md). Code: `backend/parsers/borders.py`.

## The problem

After ADR-040 the 1985 patent specification (P85) is OCR'd from its pixels, and its
scans carry a solid black band across the top and bottom of almost every page
(some pages sit in a full black frame). Tesseract read the speckle in them as junk:
P85 p6 gained `t snes ee nsn bago ey set et ines …` and scored wer 0.110.

## Survey (no references used)

Every scanned page of the 16 test PDFs, 2 mm cells on a 36 dpi render. Solid black
regions come in four kinds: P85's bands (reaching 5.6–16.5% in from the edge, full
width); hairline strips along the very edge of some PBR scans (≤ 2% deep); printed
black header bars in the PBR journals (white margin at both ends); photos and
banners inside the page.

## Three versions

| Version | Fires on | What went wrong |
|---|---|---|
| 1. long + in outer margin, re-rasterised copy, column filled min→max | 25 P85 + 49 PBR pages | P85 p17 (black frame) whitened top to bottom: 606 → 18 words |
| 2. as 1, fill only contiguous runs (holes ≤ 3 cells) | same | Re-rasterising resampled the whole page; V2 p6 0.009 → 0.068, V2 p17 changed layout path |
| 3. **vector white rectangles on a copy; band must run off the page and be ≥ 4% deep** | **25 P85 pages, nothing else** | — (version 2 with vector rectangles still lost a heading on V2 p23 and a caption on V1N3 p7 by removing hairline strips) |

## Results (version 3, shipped)

**Scored pages, all sets A–F and L (76 pages):** only P85 p6 and p21 change; the
other 74 are byte-identical. `no-borders` reproduces the previous default on all 76.

| Page | wer | cer | word precision |
|---|---|---|---|
| P85 p6 | 0.110 → **0.053** | 0.062 → 0.027 | 0.917 → 0.968 |
| P85 p21 | 0.086 → 0.082 | 0.025 → 0.023 | 0.946 → 0.950 |
| set E (12) | 0.084 → **0.079** | 0.033 → 0.029 | 0.944 → 0.949 |

**P85's 23 unreferenced pages, against the PDF's own hidden layer as a silver
reference** (`silver_layer.py`; the layer scored 0.014 / 0.045 on the referenced
pages): mean wer 0.585 → **0.440**, precision 0.719 → 0.772, recall 0.871 → 0.851.
Better on 17 pages (p10 0.753 → 0.115, p25 1.702 → 0.670), worse on p5 (0.529 →
0.773) and marginally on p14 and p19.

## The one real loss, and why

P85 p5 (the cover form) loses "Address of Applicant: New Murphy Road and Concord
Pike". Tesseract's layout pass (psm 3) still reads it. But the ADR-038 guard
(MuPDF's OCR) now reads *more* junk out of the stamp box (243 words against psm
3's 192), so "psm 3 has under 85% of the guard's words" fires and the guard's
worse text is returned. The cleanup flips that choice on 9 of the 25 pages, 7 of
them toward psm 3 (good). The guard counts junk as words; making it count only
plausible words is the next fix, with its own held-out check, because it can
change the path of every scan.

Some of the recall drop is also not a loss: on p17, p24 and p25 a strip of the
*neighbouring* page is visible at the scan's edge, and the layer includes it.

## Checks

- 529 tests pass, 8 skipped (10 new in `tests/test_borders.py`: edge band, side
  band, frame, mid-page banner, short corner block, thin rule, printed header bar,
  hairline edge strip, no-band page, determinism).
- Deterministic: pure pixel arithmetic, vector overlay.
- Cost: one extra 36 dpi render per OCR'd page to look for bands, about 0.02 s
  (measured on Vol 5 No 1).

## Not done

- The guard fix above.
- Text from a neighbouring page caught at a scan's edge (P85 p17, p24, p25) is
  still OCR'd.
- The faint grey fringe left where a band's edge was can still give a few junk
  characters (`rm rte eee` on P85 p6).
- The rest of item 1.2 (deskew, contrast, Sauvola, denoise) is not started.
