"""Convert whole PDFs with the default path, one Markdown file each (ADR-043's gate).

The scored pages are one-page PDFs, so they never exercise how a document's pages
are joined, PyMuPDF4LLM's whole-document fast path, or the mixed text-layer/OCR
path. This does: point it at two checkouts' `backend/` and `cmp` the outputs.

  python benchmarks/bakeoff/whole_docs.py BACKEND_DIR OUT_DIR PDF [PDF ...]

Run several in parallel only with one thread per Tesseract (the default since
ADR-042). No external service is called.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> None:
    backend, out = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(backend))
    from parsers.file_parser import file_to_markdown

    out.mkdir(parents=True, exist_ok=True)
    for pdf in map(Path, sys.argv[3:]):
        started = time.time()
        md = file_to_markdown(pdf.read_bytes(), "doc.pdf", engine="pymupdf")
        (out / f"{pdf.stem}.md").write_text(md, encoding="utf-8")
        print(f"{pdf.name}: {len(md)} chars, {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
