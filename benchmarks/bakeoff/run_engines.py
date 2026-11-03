"""Run one engine over a sample, one page at a time.

Each page is cut into its own single-page PDF and converted on its own, so a
page's time is that page's alone. Outputs go to outputs/<engine>/<page>.md and
timings to outputs/<engine>/timings.json. Run engines one after another, never
side by side: concurrent runs on a small box inflate every timing (post-mortem
2026-11-01).

  python benchmarks/bakeoff/run_engines.py pymupdf   # wiseau default path, in-process
  python benchmarks/bakeoff/run_engines.py docling   # docling library, EasyOCR
  python benchmarks/bakeoff/run_engines.py glm-ocr [URL]  # glm-ocr/service.py over HTTP
  python benchmarks/bakeoff/run_engines.py tesseract-psm3     # Tesseract's own layout analysis
  python benchmarks/bakeoff/run_engines.py tesseract-unordered  # default path before ADR-035

Add `--set b` or `--set c` to run the second or third independently transcribed
sample (SAMPLE_B / SAMPLE_C in regions.py) instead of the original one. Timings are merged into the engine's
timings.json, so running one set never erases the other's.

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
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C  # noqa: E402

_SET = sys.argv[sys.argv.index("--set") + 1].lower() if "--set" in sys.argv else "a"
ACTIVE = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C}[_SET]


def pages():
    for doc_key, nums in ACTIVE.items():
        doc = pymupdf.open(ROOT / DOCS[doc_key])
        for n in nums:
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
            yield f"{doc_key}_p{n}", one.tobytes()


def pymupdf_engine():
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers.file_parser import file_to_markdown

    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def tesseract_adr035_engine():
    """The default path as it stood at ADR-035, before the photo and row fixes (ADR-037)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr, reading_order
    from parsers.file_parser import file_to_markdown

    if hasattr(ocr, "SKIP_PHOTO_REGIONS"):
        ocr.SKIP_PHOTO_REGIONS = False
    if hasattr(reading_order, "ROWS_BY_CENTRE"):
        reading_order.ROWS_BY_CENTRE = False
    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def tesseract_unordered_engine():
    """The default path as it was before ADR-035: MuPDF's own line order."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr
    from parsers.file_parser import file_to_markdown

    ocr.order_lines = lambda lines: None
    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def tesseract_psm3_engine():
    """Tesseract's own page layout analysis (psm 3) on a 300 dpi render, via the CLI.

    The alternative to ADR-035's geometric re-ordering: let Tesseract decide the
    reading order itself.
    """
    import subprocess

    def run(data: bytes) -> str:
        page = pymupdf.open(stream=data, filetype="pdf")[0]
        png = page.get_pixmap(dpi=300).tobytes("png")
        return subprocess.run(["tesseract", "stdin", "stdout", "--psm", "3", "-l", "eng"], input=png,
                              capture_output=True, check=True).stdout.decode()

    return run


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
        url = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "http://127.0.0.1:7860"
        run = glm_engine(url)
    elif engine == "tesseract-psm3":
        run = tesseract_psm3_engine()
    elif engine == "tesseract-adr035":
        run = tesseract_adr035_engine()
    elif engine == "tesseract-unordered":
        run = tesseract_unordered_engine()
    else:
        raise SystemExit(f"unknown engine {engine}")
    out = HERE / "outputs" / engine
    out.mkdir(parents=True, exist_ok=True)
    timings_file = out / "timings.json"
    timings = json.loads(timings_file.read_text()) if timings_file.exists() else {}
    for key, data in pages():
        t = time.perf_counter()
        md = run(data)
        timings[key] = round(time.perf_counter() - t, 1)
        (out / f"{key}.md").write_text(md)
        print(f"{engine} {key}: {timings[key]} s, {len(md)} chars", flush=True)
    timings_file.write_text(json.dumps(timings, indent=2) + "\n")


if __name__ == "__main__":
    main()
