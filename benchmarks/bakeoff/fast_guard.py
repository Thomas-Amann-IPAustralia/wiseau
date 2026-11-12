"""Which cheap check can stand in for the ADR-038 guard on the fast engine (ADR-059)?

The guard re-reads a scanned page with MuPDF's OCR (about 4.5 s) and keeps that
reading when Tesseract psm 3 has under 85% of its plausible words (ADR-042): psm 3
sometimes throws a column away. The fast engine needs the protection without the
cost. For every page of the ADR-042 guard cache (336 scanned pages of the 16 test
PDFs) this records, per page:

- psm 3 at 300 dpi: its plausible word count and time (the fast engine's reading);
- the full guard's verdict, from the cached 300 dpi guard text (the truth here);
- MuPDF's OCR at 100 and 150 dpi: count and time (a cheaper second reading);
- `ink_out`: the share of the page's ink (50 dpi, grey < 128, photo cells left
  out) lying outside psm 3's word boxes padded by 3 pt. A dropped column is ink
  that psm 3 put no word on, and this costs no OCR at all.

  python benchmarks/bakeoff/fast_guard.py OUT.json [--jobs 4]

Resumable. Seconds are rough when --jobs > 1 (pages run side by side).
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "benchmarks/bakeoff"))
import pymupdf  # noqa: E402

from regions import DOCS  # noqa: E402
from parsers import borders, ocr, photo_regions, tesseract_layout as tl  # noqa: E402
from parsers.serializer import render_elements  # noqa: E402

CACHE = ROOT / "benchmarks/bakeoff/survey/guard-cache-2026-11-09.json.gz"
INK_DPI = 50
INK_LEVEL = 128
PAD_PT = 3.0


def ink_outside(page: pymupdf.Page, words, mask) -> float:
    """Share of ink pixels (photo cells excluded) outside every padded word box."""
    pix = page.get_pixmap(dpi=INK_DPI, colorspace=pymupdf.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    k = INK_DPI / 72.0
    covered = bytearray(w * h)
    for wd in words:
        x0, y0 = max(0, int((wd.x0 - PAD_PT) * k)), max(0, int((wd.y0 - PAD_PT) * k))
        x1, y1 = min(w, int((wd.x1 + PAD_PT) * k) + 1), min(h, int((wd.y1 + PAD_PT) * k) + 1)
        for y in range(y0, y1):
            covered[y * w + x0:y * w + x1] = b"\x01" * (x1 - x0)
    cells, cs = (mask.cells, mask.scale) if mask is not None else (frozenset(), 1.0)
    ink = out = 0
    for y in range(h):
        base = y * w
        for x in range(w):
            if s[base + x] < INK_LEVEL:
                if cells and (int(x / k * cs), int(y / k * cs)) in cells:
                    continue
                ink += 1
                if not covered[base + x]:
                    out += 1
    return out / ink if ink else 0.0


def survey(item):
    key, guard_text = item
    doc_key, n = key.rsplit("_p", 1)
    page = pymupdf.open(ROOT / DOCS[doc_key])[int(n) - 1]
    cleaned = borders.cleaned_page(page)
    if cleaned is not None:
        _d, page = cleaned
    mask = photo_regions.photo_mask(page)
    t = time.perf_counter()
    read = tl.read_page_layout(page, dpi=300, language="eng", mask=mask)
    psm3_s = time.perf_counter() - t
    if read is None:
        return key, None
    text = render_elements(ocr.layout_elements(tl.groups(read.blocks)))
    row = {"psm3": tl.plausible_word_count(text), "psm3_s": round(psm3_s, 2),
           "guard300": tl.plausible_word_count(guard_text),
           "fires": not tl.keeps_words(text, guard_text)}
    for dpi in (100, 150):
        t = time.perf_counter()
        g = render_elements(ocr.TesseractEngine._geometric_elements(page, dpi=dpi, language="eng", mask=mask))
        row[f"guard{dpi}"] = tl.plausible_word_count(g)
        row[f"guard{dpi}_s"] = round(time.perf_counter() - t, 2)
        row[f"fires{dpi}"] = not tl.keeps_words(text, g)
    t = time.perf_counter()
    row["ink_out"] = round(ink_outside(page, read.words, mask), 4)
    row["ink_s"] = round(time.perf_counter() - t, 3)
    return key, row


def main() -> None:
    out = Path(sys.argv[1])
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 1
    done = json.loads(out.read_text()) if out.exists() else {}
    cache = json.load(gzip.open(CACHE))
    todo = [(k, v["guard"]) for k, v in sorted(cache.items()) if k not in done and v.get("psm3") is not None]
    with Pool(jobs) as pool:
        for key, row in pool.imap_unordered(survey, todo):
            done[key] = row
            out.write_text(json.dumps(done, indent=0, sort_keys=True))
            print(key, row, flush=True)


if __name__ == "__main__":
    main()
