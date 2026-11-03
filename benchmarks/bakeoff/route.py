"""A reference-free, per-page router between Tesseract and GLM-OCR for scanned pages.

The rule uses only what the default path already knows after its ~3 s Tesseract
pass (no reference, no second engine):

* **number share >= 0.3** (a table or list of figures) -> keep Tesseract
* **single column** (no ADR-035 gutter found)          -> keep Tesseract
* otherwise (multi-column prose, prose + data, photos) -> GLM-OCR

It was written after looking at set B's per-group results, so set B flatters it;
set A, whose pages played no part in choosing it, is the fairer check. Born-digital
pages are not routed: they go to PyMuPDF (ADR-036) and are left out here.

  python benchmarks/bakeoff/route.py     # writes outputs/router-rule/ (needs tesseract)
  python benchmarks/bakeoff/score.py [--set b]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))
from regions import DOCS, SAMPLE, SAMPLE_B  # noqa: E402
from parsers import reading_order  # noqa: E402
from parsers.ocr import _ocr_lines  # noqa: E402

NUMBER_SHARE = 0.3


def route(page: pymupdf.Page) -> tuple[str, float, int]:
    """(engine, number share, column count) for one image-only page."""
    textpage = page.get_textpage_ocr(flags=0, language="eng", dpi=300, full=True)
    lines = [ln for ln in _ocr_lines(page, textpage) if ln.text.strip()]
    words = re.findall(r"[^\W_]+", " ".join(ln.text for ln in lines).lower())
    share = sum(any(c.isdigit() for c in w) for w in words) / max(len(words), 1)
    columns = len(reading_order._choose_gutters(lines)) + 1 if len(lines) >= reading_order.MIN_LINES else 1
    engine = "pymupdf" if share >= NUMBER_SHARE or columns == 1 else "glm-ocr"
    return engine, round(share, 2), columns


def main() -> None:
    out = HERE / "outputs" / "router-rule"
    out.mkdir(parents=True, exist_ok=True)
    picks, timings = {}, {}
    for doc_key, nums in list(SAMPLE.items()) + list(SAMPLE_B.items()):
        if doc_key == "V16":
            continue  # born-digital: PyMuPDF, not routed
        doc = pymupdf.open(ROOT / DOCS[doc_key])
        for n in nums:
            key = f"{doc_key}_p{n}"
            engine, share, columns = route(doc[n - 1])
            picks[key] = {"engine": engine, "number_share": share, "columns": columns}
            (out / f"{key}.md").write_text((HERE / "outputs" / engine / f"{key}.md").read_text())
            spent = json.loads((HERE / "outputs" / "pymupdf" / "timings.json").read_text())[key]
            if engine == "glm-ocr":  # Tesseract always runs first; GLM-OCR only when routed
                spent += json.loads((HERE / "outputs" / "glm-ocr" / "timings.json").read_text())[key]
            timings[key] = round(spent, 1)
            print(key, picks[key])
    (out / "picks.json").write_text(json.dumps(picks, indent=2) + "\n")
    (out / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")


if __name__ == "__main__":
    main()
