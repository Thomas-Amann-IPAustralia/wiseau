"""Every Table Transformer region on the scored scans, with features and its effect (ADR-046).

For designing and checking `tables.is_tabular`. For each scanned page of the
given sets this runs the default path up to psm 3's reading, then reads every
region the detector finds (no tabularity check), and for each region alone
measures what reading it as a table does to the page against its reference:
wer and ord with only that region taken, against the page without it. Also
times detection and structure recognition.

  python benchmarks/bakeoff/tatr_regions.py OUT.json a b c d l   # development sets
  python benchmarks/bakeoff/tatr_regions.py OUT.json e f         # held out

Needs the backend requirements plus requirements-tables.txt. No external service
is called apart from the one-off model download.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

import pymupdf  # noqa: E402
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L  # noqa: E402
from score import score_page  # noqa: E402

from parsers import borders, ocr, photo_regions, serializer, tables, tesseract_layout  # noqa: E402
from parsers.cleaner import clean_markdown  # noqa: E402
from parsers.file_parser import _page_needs_ocr  # noqa: E402

SETS = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F, "l": SAMPLE_L}
DPI, LANG = 300, "eng"


def page_layout(page: pymupdf.Page):
    """What `TesseractEngine.read_elements` sees: cleaned page, psm 3 elements, words."""
    cleaned = borders.cleaned_page(page)
    keep = None
    if cleaned is not None:
        keep, page = cleaned
    mask = photo_regions.photo_mask(page)
    guard = ocr.TesseractEngine._geometric_elements(page, dpi=DPI, language=LANG, mask=mask)
    read = tesseract_layout.read_page_layout(page, dpi=DPI, language=LANG, mask=mask)
    if read is None:
        return keep, page, None, None
    layout = ocr.layout_elements(tesseract_layout.groups(read.blocks))
    if not tesseract_layout.keeps_words(serializer.render_elements(layout), serializer.render_elements(guard)):
        return keep, page, None, None   # the guard wins: the table pass does not run
    return keep, page, layout, read.words


def main() -> None:
    out_path = Path(sys.argv[1])
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    real_check = tables.is_tabular
    tables.is_tabular = lambda f: True   # read every region; the check is applied below
    for s in sys.argv[2:]:
        for doc_key, nums in SETS[s].items():
            doc = pymupdf.open(ROOT / DOCS[doc_key])
            for n in nums:
                key = f"{doc_key}_p{n}"
                one = pymupdf.open()
                one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
                if not _page_needs_ocr(one[0]):
                    continue
                ref = (HERE / "references" / f"{key}.txt").read_text()
                _keep, page, layout, words = page_layout(one[0])
                if layout is None:
                    results[key] = {"set": s, "guard": True, "regions": []}
                    print(key, "guard text; no table pass", flush=True)
                    continue
                base_md = clean_markdown(serializer.render_elements(layout))
                base = score_page(ref, base_md)
                t = time.perf_counter()
                regions = tables.read_tables(page, words, [ln for el in layout for ln in el.lines])
                seconds = time.perf_counter() - t
                rows = []
                both = clean_markdown(serializer.render_elements(tables.apply(
                    layout, [x._replace(accepted=bool(x.rows) and real_check(x.features)) for x in regions], words)))
                checked = score_page(ref, both)
                for i, r in enumerate(regions):
                    alone = [x._replace(accepted=(j == i)) for j, x in enumerate(regions)]
                    md = clean_markdown(serializer.render_elements(tables.apply(layout, alone, words)))
                    sc = score_page(ref, md)
                    rows.append({"box": [round(v, 1) for v in r.box], "score": round(r.score, 3),
                                 "features": r.features, "check": real_check(r.features) if r.rows else False,
                                 "lines": len(r.lines),
                                 "d_wer": round(sc["wer"] - base["wer"], 4),
                                 "d_ord": None if sc["ord"] is None or base["ord"] is None
                                 else round(sc["ord"] - base["ord"], 4)})
                results[key] = {"set": s, "guard": False, "base_wer": round(base["wer"], 4),
                                "checked_wer": round(checked["wer"], 4),
                                "seconds": round(seconds, 2), "regions": rows}
                print(key, f"{seconds:.1f}s", [(x["features"].get("words_per_cell"), x["d_wer"]) for x in rows],
                      flush=True)
                out_path.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    out_path.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
