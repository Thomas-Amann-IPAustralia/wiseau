"""Run one engine over a sample, one page at a time.

Each page is cut into its own single-page PDF and converted on its own, so a
page's time is that page's alone. Outputs go to outputs/<engine>/<page>.md and
timings to outputs/<engine>/timings.json. Run engines one after another, never
side by side: concurrent runs on a small box inflate every timing (post-mortem
2026-11-01).

  python benchmarks/bakeoff/run_engines.py pymupdf   # wiseau default path, in-process
  python benchmarks/bakeoff/run_engines.py docling   # docling library, EasyOCR; also writes <page>.json (its document tree)
  python benchmarks/bakeoff/run_engines.py glm-ocr [URL]  # glm-ocr/service.py over HTTP
  python benchmarks/bakeoff/run_engines.py tesseract-psm3     # Tesseract's own layout analysis
  python benchmarks/bakeoff/run_engines.py tesseract-unordered  # default path before ADR-035
  python benchmarks/bakeoff/run_engines.py tesseract-layout     # ADR-038: psm 3 + row stitching + guard
  python benchmarks/bakeoff/run_engines.py tesseract-adr037     # default path before ADR-038
  python benchmarks/bakeoff/run_engines.py hidden-layer   # the page's own (invisible OCR) text layer, as is
  python benchmarks/bakeoff/run_engines.py forced-ocr     # default path, but OCR every page (ignore the layer)
  python benchmarks/bakeoff/run_engines.py trust-layer    # default path before ADR-040 (reads a scan's invisible layer)
  python benchmarks/bakeoff/run_engines.py no-borders     # default path before scanner-band cleanup
  python benchmarks/bakeoff/run_engines.py pre-VARIANT    # default path on preprocessed pixels (preprocess.py;
                                                          #   ident, deskew, norm, sauvola, denoise; roadmap 1.2)
  python benchmarks/bakeoff/run_engines.py rec-MODEL      # psm 3 layout, lines re-read by MODEL (ml_engines.py;
                                                          #   tesseract = control, PP-OCRv6_small_rec, ...; roadmap 1.3)
  python benchmarks/bakeoff/run_engines.py wrec-MODEL     # as rec-MODEL, but each Tesseract word box re-read
  python benchmarks/bakeoff/run_engines.py tatr           # psm 3 layout, tables found by Table Transformer (1.3)
  python benchmarks/bakeoff/run_engines.py ppstructure-v3 # PaddleOCR's PP-StructureV3 pipeline (1.3)

Add `--pages KEY,KEY` to run only those pages of the set. Add `--set b`, `--set c`, `--set d`, `--set e`, `--set f` or `--set l` to run another independently
transcribed sample (SAMPLE_B ... SAMPLE_L in regions.py) instead of the original one. Timings are merged into the engine's
timings.json, so running one set never erases the other's.

Each page's run record also goes to outputs/<engine>/runs.json (merged the same
way): seconds, peak resident memory so far in MB (the high-water mark of this
process or of its largest child, such as the `tesseract` CLI; an HTTP engine's
server is not visible), and whether the page raised. A page that raises is
recorded as failed, gets no .md, and the run carries on; score.py scores it as
empty output and reports the failure rate. The exit status is 1 if any page failed.

No engine here calls a paid or external API.
"""
from __future__ import annotations

import json
import resource
import sys
import time
import traceback
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L  # noqa: E402

_SET = sys.argv[sys.argv.index("--set") + 1].lower() if "--set" in sys.argv else "a"
ACTIVE = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F,
          "l": SAMPLE_L}[_SET]


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


def hidden_layer_engine():
    """The page's own text layer, as is: no OCR, no Markdown (roadmap 14 item 0.2).

    On a scan with an invisible OCR layer (Acrobat Paper Capture, or the patent
    PDFs' unattributed layer) this is exactly the text that layer holds, which the
    default path passes through whenever it has 16+ characters. An image-only page
    yields nothing.
    """
    return lambda data: pymupdf.open(stream=data, filetype="pdf")[0].get_text()


def no_borders_engine():
    """The default path before scanner-band cleanup (borders.py): bands are OCR'd as they are."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr
    from parsers.file_parser import file_to_markdown

    if hasattr(ocr, "BORDER_CLEANUP"):
        ocr.BORDER_CLEANUP = False
    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def trust_layer_engine():
    """The default path before ADR-040: a scan's invisible OCR layer is read as its text."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import file_parser

    if hasattr(file_parser, "OCR_INVISIBLE_LAYER"):
        file_parser.OCR_INVISIBLE_LAYER = False
    return lambda data: file_parser.file_to_markdown(data, "page.pdf", engine="pymupdf")


def forced_ocr_engine():
    """The default path with WISEAU_OCR_MODE=force: every page is OCR'd from its
    pixels by the ADR-038 path, ignoring any text layer (roadmap 14 item 0.2)."""
    import os

    os.environ["WISEAU_OCR_MODE"] = "force"
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers.file_parser import file_to_markdown

    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def preprocess_engine(variant: str):
    """The default path on a page whose pixels went through ``preprocess.apply(variant)``.

    Only pages the default path would OCR are changed; a born-digital page is
    converted as it is, so it scores the same as ``pymupdf``.
    """
    sys.path.insert(0, str(ROOT / "backend"))
    import preprocess
    from parsers.file_parser import _page_needs_ocr, file_to_markdown

    if variant not in preprocess.VARIANTS:
        raise SystemExit(f"unknown preprocessing variant {variant}; one of {preprocess.VARIANTS}")

    def run(data: bytes) -> str:
        if _page_needs_ocr(pymupdf.open(stream=data, filetype="pdf")[0]):
            data = preprocess.apply(variant, data)
        return file_to_markdown(data, "page.pdf", engine="pymupdf")

    return run


def tesseract_adr035_engine():
    """The default path as it stood at ADR-035, before the photo and row fixes (ADR-037)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr, reading_order
    from parsers.file_parser import file_to_markdown

    if hasattr(ocr, "SKIP_PHOTO_REGIONS"):
        ocr.SKIP_PHOTO_REGIONS = False
    if hasattr(reading_order, "ROWS_BY_CENTRE"):
        reading_order.ROWS_BY_CENTRE = False
    if hasattr(ocr, "TESSERACT_LAYOUT"):
        ocr.TESSERACT_LAYOUT = False
    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def tesseract_layout_engine():
    """The ADR-038 candidate: Tesseract psm 3 + table-row stitching, ADR-037 text as guard."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr
    from parsers.file_parser import file_to_markdown

    ocr.TESSERACT_LAYOUT = True
    return lambda data: file_to_markdown(data, "page.pdf", engine="pymupdf")


def tesseract_adr037_engine():
    """The default path as it stood at ADR-037, before Tesseract's own layout (ADR-038)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from parsers import ocr
    from parsers.file_parser import file_to_markdown

    if hasattr(ocr, "TESSERACT_LAYOUT"):
        ocr.TESSERACT_LAYOUT = False
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

    def run(data: bytes) -> tuple[str, dict]:
        # The document tree as well as its Markdown (roadmap 14 item 0.4): it keeps
        # the element types, bounding boxes and table spans the Markdown flattens.
        res = conv.convert(DocumentStream(name="page.pdf", stream=BytesIO(data)))
        return res.document.export_to_markdown(), res.document.export_to_dict()

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
    wants_key = False   # ml_engines' engines also take the page key
    if engine == "pymupdf":
        run = pymupdf_engine()
    elif engine == "docling":
        run = docling_engine()
    elif engine == "glm-ocr":
        url = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "http://127.0.0.1:7860"
        run = glm_engine(url)
    elif engine == "hidden-layer":
        run = hidden_layer_engine()
    elif engine == "no-borders":
        run = no_borders_engine()
    elif engine == "trust-layer":
        run = trust_layer_engine()
    elif engine == "forced-ocr":
        run = forced_ocr_engine()
    elif engine == "tesseract-psm3":
        run = tesseract_psm3_engine()
    elif engine == "tesseract-adr035":
        run = tesseract_adr035_engine()
    elif engine == "tesseract-layout":
        run = tesseract_layout_engine()
    elif engine == "tesseract-adr037":
        run = tesseract_adr037_engine()
    elif engine == "tesseract-unordered":
        run = tesseract_unordered_engine()
    elif engine.startswith("pre-"):
        run = preprocess_engine(engine[4:])
    elif engine.startswith("rec-"):
        import ml_engines
        run, wants_key = ml_engines.rec_engine(engine[4:]), True
    elif engine.startswith("wrec-"):
        import ml_engines
        run, wants_key = ml_engines.word_rec_engine(engine[5:]), True
    elif engine == "tatr":
        import ml_engines
        run, wants_key = ml_engines.tatr_engine(), True
    elif engine == "ppstructure-v3":
        import ml_engines
        run, wants_key = ml_engines.ppstructure_engine(), True
    else:
        raise SystemExit(f"unknown engine {engine}")
    out = HERE / "outputs" / engine
    out.mkdir(parents=True, exist_ok=True)
    timings_file = out / "timings.json"
    runs_file = out / "runs.json"
    timings = json.loads(timings_file.read_text()) if timings_file.exists() else {}
    runs = json.loads(runs_file.read_text()) if runs_file.exists() else {}
    failures = 0
    only = set(sys.argv[sys.argv.index("--pages") + 1].split(",")) if "--pages" in sys.argv else None
    for key, data in pages():
        if only is not None and key not in only:
            continue
        t = time.perf_counter()
        tree = None
        try:
            md, error = (run(data, key) if wants_key else run(data)), None
            if isinstance(md, tuple):          # an engine that also returns its document tree
                md, tree = md
        except Exception as exc:  # recorded, not fatal: a failure rate needs the rest of the run
            md, error = None, f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        seconds = round(time.perf_counter() - t, 1)
        runs[key] = {"seconds": seconds, "rss_mb": peak_rss_mb(), "ok": error is None, "error": error}
        if error is None:
            timings[key] = seconds
            (out / f"{key}.md").write_text(md)
            if tree is not None:
                (out / f"{key}.json").write_text(json.dumps(tree, indent=1, sort_keys=True) + "\n")
            print(f"{engine} {key}: {seconds} s, {len(md)} chars, peak {runs[key]['rss_mb']} MB", flush=True)
        else:
            failures += 1
            (out / f"{key}.md").unlink(missing_ok=True)   # never score a stale output as this run's
            timings.pop(key, None)
            print(f"{engine} {key}: FAILED after {seconds} s ({error})", flush=True)
    timings_file.write_text(json.dumps(timings, indent=2) + "\n")
    runs_file.write_text(json.dumps(runs, indent=2, sort_keys=True) + "\n")
    if failures:
        raise SystemExit(f"{failures} page(s) failed; see {runs_file}")


def peak_rss_mb() -> float:
    """High-water resident memory of this process or its largest child, in MB (Linux: ru_maxrss is KB)."""
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(max(own, child) / 1024, 1)


if __name__ == "__main__":
    main()
