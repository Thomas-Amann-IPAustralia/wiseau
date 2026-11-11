"""Convert whole PDFs with the image's path and save each document's IR (Tier 3 audits).

The structure passes (`parsers/recover.py`) are judged on whole documents as
well as on the gold pages: repeated running heads, paragraphs and tables that
continue onto the next page, and footnotes only show up across pages. This
writes `<OUT_DIR>/<stem>.json` (the IR before any structure pass) and the PDF's
outline as `<stem>.outline.json`, so the passes can be re-run offline.

  WISEAU_TABLES=tatr WISEAU_LAYOUT=ppdl \\
  python benchmarks/bakeoff/whole_ir.py OUT_DIR PDF [PDF ...]

No external service is called.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))


def main() -> None:
    import pymupdf
    from parsers.file_parser import file_to_document

    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for pdf in map(Path, sys.argv[2:]):
        started = time.time()
        data = pdf.read_bytes()
        doc = file_to_document(data, "doc.pdf", engine="pymupdf")
        stem = pdf.stem.replace(" ", "_")
        (out / f"{stem}.json").write_text(json.dumps(doc.to_dict(), indent=1, sort_keys=True) + "\n")
        with pymupdf.open(stream=data, filetype="pdf") as src:
            (out / f"{stem}.outline.json").write_text(json.dumps(src.get_toc()) + "\n")
        print(f"{pdf.name}: {len(doc.pages)} pages, {len(doc.elements)} elements, "
              f"{time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
