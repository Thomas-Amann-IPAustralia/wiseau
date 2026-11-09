"""What the Table Transformer pass costs: time per page, model load, memory (ADR-046).

Converts the same scanned pages through the default path with the pass off and
on, one page at a time, in one process, alternating off/on per page so machine
drift hits both alike. Run it alone on the machine (no other jobs).

  python benchmarks/bakeoff/table_timing.py OUT.json [--threads N]

Reports per page: seconds off, seconds on, and the pass's own share (detection +
structure); then the cold model load and the process's peak resident memory
before and after the models load.
"""
from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

import pymupdf  # noqa: E402
from regions import DOCS, SAMPLE_S  # noqa: E402


def rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def main() -> None:
    out = Path(sys.argv[1])
    if "--threads" in sys.argv:
        os.environ["WISEAU_TABLE_THREADS"] = sys.argv[sys.argv.index("--threads") + 1]
    from parsers import tables
    from parsers.file_parser import _page_needs_ocr, file_to_markdown

    tables.THREADS = int(os.environ.get("WISEAU_TABLE_THREADS", "1"))
    pages = []
    for doc_key, nums in SAMPLE_S.items():
        doc = pymupdf.open(ROOT / DOCS[doc_key])
        for n in nums:
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
            if _page_needs_ocr(one[0]):
                pages.append((f"{doc_key}_p{n}", one.tobytes()))

    # Warm the default path (Tesseract, imports) before timing anything.
    os.environ.pop("WISEAU_TABLES", None)
    file_to_markdown(pages[0][1], "p.pdf", engine="pymupdf")
    rss_before = rss_mb()
    t = time.perf_counter()
    tables._load()
    load = time.perf_counter() - t
    rss_models = rss_mb()

    # Time the pass's own work by wrapping its two model calls.
    spent = {"s": 0.0}
    real_detect = tables.detect

    def timed_detect(page):
        t0 = time.perf_counter()
        try:
            return real_detect(page)
        finally:
            spent["s"] += time.perf_counter() - t0

    tables.detect = timed_detect
    rows = {}
    for key, data in pages:
        os.environ.pop("WISEAU_TABLES", None)
        t = time.perf_counter()
        file_to_markdown(data, "p.pdf", engine="pymupdf")
        off = time.perf_counter() - t
        os.environ["WISEAU_TABLES"] = "tatr"
        spent["s"] = 0.0
        t = time.perf_counter()
        file_to_markdown(data, "p.pdf", engine="pymupdf")
        on = time.perf_counter() - t
        rows[key] = {"off": round(off, 2), "on": round(on, 2), "tatr": round(spent["s"], 2)}
        print(key, rows[key], flush=True)
    summary = {
        "threads": tables.THREADS,
        "pages": len(rows),
        "mean_off": round(sum(r["off"] for r in rows.values()) / len(rows), 2),
        "mean_on": round(sum(r["on"] for r in rows.values()) / len(rows), 2),
        "mean_tatr": round(sum(r["tatr"] for r in rows.values()) / len(rows), 2),
        "model_load_s": round(load, 1),
        "rss_mb_before_models": rss_before,
        "rss_mb_with_models": rss_models,
        "rss_mb_peak": rss_mb(),
    }
    print(json.dumps(summary, indent=1))
    out.write_text(json.dumps({"summary": summary, "pages": rows}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
