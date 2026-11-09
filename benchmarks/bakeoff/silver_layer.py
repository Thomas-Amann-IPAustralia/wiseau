"""Score an OCR switch on pages nobody has transcribed, against the PDF's own text layer.

Hand-made references cover only a few pages of a PDF. When the PDF also carries a
*good* hidden OCR layer (the 1985 patent's scored wer 0.014 and 0.045 against blind
references), that layer is a usable silver reference for the rest of its pages: not
ground truth, but enough to see whether a change helps or hurts across many pages.

For each page, the default OCR engine runs with a module flag off and then on, and
both are scored against the page's layer (wer, word recall, word precision, with
`score.py`'s tokenisation). Pages whose layer has under 50 words are skipped.

  python benchmarks/bakeoff/silver_layer.py PDF FLAG [--pages 1,2,5]

FLAG is an attribute of `parsers.ocr`, e.g. BORDER_CLEANUP (ADR-041). Remember the
layer's own blind spots: it can include text from a neighbouring page caught at the
edge of the scan, which no OCR of *this* page should be blamed for missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "backend"))
from score import edit_distance, overlap, tokens  # noqa: E402

from parsers import ocr  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    pdf, flag = args[0], args[1]
    doc = pymupdf.open(pdf)
    pages = ([int(n) for n in args[args.index("--pages") + 1].split(",")] if "--pages" in args
             else range(1, doc.page_count + 1))
    engine = ocr.TesseractEngine()
    total = {False: [0.0, 0.0, 0.0], True: [0.0, 0.0, 0.0]}
    scored = 0
    for n in pages:
        page = doc[n - 1]
        silver = tokens(page.get_text())
        if len(silver) < 50:
            continue
        scored += 1
        cells = []
        for on in (False, True):
            setattr(ocr, flag, on)
            hyp = tokens(engine.ocr_page(page, dpi=300, language="eng"))
            recall, precision = overlap(silver, hyp)
            wer = edit_distance(silver, hyp) / len(silver)
            for i, v in enumerate((wer, recall, precision)):
                total[on][i] += v
            cells.append((wer, recall, precision))
        (w0, r0, p0), (w1, r1, p1) = cells
        print(f"p{n}: {len(silver)} words | wer {w0:.3f} -> {w1:.3f} | recall {r0:.3f} -> {r1:.3f} "
              f"| precision {p0:.3f} -> {p1:.3f}")
    for on in (False, True):
        w, r, p = (v / max(1, scored) for v in total[on])
        print(f"MEAN {flag}={on}: wer {w:.3f} recall {r:.3f} precision {p:.3f} over {scored} pages")


if __name__ == "__main__":
    main()
