"""Whole-document wall time of our engines on the PDFs the user timed in Acrobat.

The user OCR'd five test PDFs with Adobe Acrobat (64-bit) 26 Paper Capture and
timed each with a stopwatch (`pdf-test/acrobat-pdf/AdobeAcrobat-OCR-Timing.txt`).
This converts the same documents end to end, the way the API does (one
`file_to_markdown` call per document), and records wall seconds, process CPU
seconds and peak memory.

Each (engine, document) runs in its own process, after a one-page warm-up
conversion, so the time is that of a warm server: model loading is not counted,
as it is not on a deployed instance after its first request. Run the documents
one after another, never side by side.

  python benchmarks/bakeoff/acrobat_timing.py fast V5N1      # one engine, one document
  python benchmarks/bakeoff/acrobat_timing.py all            # every engine x document

Engines: `fast` (ADR-059) and `accurate` (the image's default path: tables,
layout and structure passes, exactly as `run_engines.py image-default`).
Results merge into outputs/acrobat-timing/timings.json; the Markdown goes beside it.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
OUT = HERE / "outputs" / "acrobat-timing"

# Volume 6 Number 1's original scan is not in pdf-test/, so it is read from the
# Acrobat copy: the default path ignores an invisible OCR layer (ADR-040) and OCRs
# the pixels, which are Acrobat's deskewed resampling of the original.
DOCS = {
    "V5N1": "pdf-test/Volume 5 Number 1.pdf",
    "V5N3": "pdf-test/Volume 5 Number 3.pdf",
    "V6N1": "pdf-test/acrobat-pdf/Volume 6 Number 1_AA-OCR.pdf",
    "V2N2": "pdf-test/Volume 2 Number 2.pdf",
    "V20N4": "pdf-test/Volume 20 Number 4.pdf",
}
ENGINES = {
    "fast": ("fast", {"WISEAU_STRUCTURE": "all"}),
    "accurate": ("pymupdf", {"WISEAU_TABLES": "tatr", "WISEAU_LAYOUT": "ppdl", "WISEAU_STRUCTURE": "all",
                             "WISEAU_TABLE_THREADS": "1", "WISEAU_LAYOUT_THREADS": "1"}),
}
WARMUP = ("pdf-test/Volume 5 Number 1.pdf", 10)   # a scanned table page


def run_one(engine: str, doc: str) -> dict:
    name, env = ENGINES[engine]
    os.environ.update(env)
    sys.path.insert(0, str(ROOT / "backend"))
    import pymupdf
    from parsers import file_parser

    src = pymupdf.open(ROOT / WARMUP[0])
    one = pymupdf.open()
    one.insert_pdf(src, from_page=WARMUP[1], to_page=WARMUP[1])
    file_parser.file_to_markdown(one.tobytes(), "warmup.pdf", engine=name)

    data = (ROOT / DOCS[doc]).read_bytes()
    pages = len(pymupdf.open(stream=data, filetype="pdf"))
    cpu0 = _cpu()
    t0 = time.perf_counter()
    md = file_parser.file_to_markdown(data, Path(DOCS[doc]).name, engine=name)
    wall = time.perf_counter() - t0
    cpu = _cpu() - cpu0
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{engine}-{doc}.md").write_text(md)
    rss = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / 1024
    return {"pages": pages, "wall_s": round(wall, 1), "cpu_s": round(cpu, 1),
            "s_per_page": round(wall / pages, 2), "peak_rss_mb": round(rss), "chars": len(md),
            "cpus": os.cpu_count()}


def _cpu() -> float:
    """CPU seconds of this process and its finished children (the tesseract CLI)."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


def main() -> None:
    if sys.argv[1] == "all":
        for doc in DOCS:
            for engine in ENGINES:
                subprocess.run([sys.executable, __file__, engine, doc], check=True)
        return
    engine, doc = sys.argv[1], sys.argv[2]
    result = run_one(engine, doc)
    path = OUT / "timings.json"
    timings = json.loads(path.read_text()) if path.exists() else {}
    timings[f"{engine}-{doc}"] = result
    path.write_text(json.dumps(timings, indent=2, sort_keys=True) + "\n")
    print(f"{engine} {doc}: {result}", flush=True)


if __name__ == "__main__":
    main()
