"""Run one engine over the 10 sample pages, one page at a time.

Each page is cut into its own single-page PDF and converted on its own, so a
page's time is that page's alone. Outputs go to outputs/<engine>/<page>.md and
timings to outputs/<engine>/timings.json. Run engines one after another, never
side by side: concurrent runs on a small box inflate every timing (post-mortem
2026-11-01).

  python benchmarks/bakeoff/run_engines.py pymupdf   # wiseau default path, in-process
  python benchmarks/bakeoff/run_engines.py docling   # docling library, EasyOCR
  python benchmarks/bakeoff/run_engines.py glm-ocr [URL]  # glm-ocr/service.py over HTTP

No engine here calls a paid or external API.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from regions import DOCS, SAMPLE  # noqa: E402


def pages():
    for doc_key, nums in SAMPLE.items():
        doc = pymupdf.open(ROOT / DOCS[doc_key])
        for n in nums:
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
            yield f"{doc_key}_p{n}", one.tobytes()


def pymupdf_engine():
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers.file_parser import file_to_markdown

    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def docling_engine():
    from io import BytesIO

    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=True, do_table_structure=True, ocr_options=EasyOcrOptions())
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    conv.convert(DocumentStream(name="warm.pdf", stream=BytesIO(next(pages())[1])))  # load models once

    def run(data: bytes) -> str:
        res = conv.convert(DocumentStream(name="page.pdf", stream=BytesIO(data)))
        return res.document.export_to_markdown()

    return run


def glm_engine(url: str):
    import requests

    def run(data: bytes) -> str:
        r = requests.post(f"{url}/v1/convert/file", files={"files": ("page.pdf", data, "application/pdf")},
                          timeout=7200)
        r.raise_for_status()
        return r.json()["markdown"]

    return run


def main() -> None:
    engine = sys.argv[1]
    if engine == "pymupdf":
        run = pymupdf_engine()
    elif engine == "docling":
        run = docling_engine()
    elif engine == "glm-ocr":
        run = glm_engine(sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:7860")
    else:
        raise SystemExit(f"unknown engine {engine}")
    out = HERE / "outputs" / engine
    out.mkdir(parents=True, exist_ok=True)
    timings = {}
    for key, data in pages():
        t = time.perf_counter()
        md = run(data)
        timings[key] = round(time.perf_counter() - t, 1)
        (out / f"{key}.md").write_text(md)
        print(f"{engine} {key}: {timings[key]} s, {len(md)} chars", flush=True)
    (out / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")


if __name__ == "__main__":
    main()
