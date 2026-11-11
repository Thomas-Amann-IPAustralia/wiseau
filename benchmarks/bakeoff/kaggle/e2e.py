"""One leg of the end-to-end GPU benchmark: the backend's real conversion, timed.

Every page goes through `parsers.file_parser.file_to_markdown` as the service
runs it: engine selection, OCR, the model passes, structure recovery, the
serializer and the normalizer. A remote engine (docling, GLM-OCR) is reached over
HTTP at the service the kernel started, as in production. A per-model timing
leaves all of that out; this does not.

  python e2e.py LEG --out DIR [--engine pymupdf|docling|glm-ocr] [--device cpu|cuda]
                    [--pages KEY,KEY] [--docs PDF,PDF] [--no-pages]

Pages are sets A-D (`regions.py`), the 42 pages with references that docling
and GLM-OCR were already scored on, each cut into its own one-page PDF as
`run_engines.py` does. `--docs` also converts whole PDFs (the multi-page path,
model load excluded). One untimed warm-up conversion runs first, and its time is
recorded as the leg's cold start.

The backend's metrics say which engine produced each conversion. A page a remote
engine fell back on is recorded as failed (`ok: false`, `fell_back`), so its
fast-parser output is never scored, or timed, as the remote engine's.

Writes DIR/<page>.md, DIR/runs.json (run_engines.py's shape, plus `engine`,
`fell_back` and `gpu_peak_mb` for in-process GPU legs) and DIR/docs.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
ROOT = BENCH.parent.parent
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "backend"))

import pymupdf  # noqa: E402
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D  # noqa: E402


def arg(name: str, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def pages():
    for sample in (SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D):
        for doc_key, nums in sample.items():
            doc = pymupdf.open(ROOT / DOCS[doc_key])
            for n in nums:
                one = pymupdf.open()
                one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
                yield f"{doc_key}_p{n}", one.tobytes()


def main() -> None:
    leg = sys.argv[1]
    out = Path(arg("--out"))
    engine = arg("--engine", "pymupdf")
    device = arg("--device", "cpu")
    out.mkdir(parents=True, exist_ok=True)

    gpu = device.startswith("cuda") and engine == "pymupdf"
    if gpu:
        import gpu_device
        gpu_device.install(device)
    from observability import metrics
    from parsers.file_parser import file_to_markdown

    def convert(data: bytes) -> tuple[str, str, bool]:
        """Markdown, the engine that produced it, and whether a remote engine fell back."""
        before = metrics.snapshot()
        md = file_to_markdown(data, "page.pdf", engine=engine)
        after = metrics.snapshot()
        produced = sorted(k for k, v in after["engines"].items() if v != before["engines"].get(k, 0))
        fell_back = False
        for key in ("docling", "glm_ocr"):
            if after[key].get("fallbacks", 0) != before[key].get("fallbacks", 0):
                fell_back = True
        return md, ",".join(produced) or "local", fell_back

    def gpu_reset():
        if gpu:
            import torch
            torch.cuda.reset_peak_memory_stats()

    def gpu_peak():
        if gpu:
            import torch
            return round(torch.cuda.max_memory_allocated() / 2**20, 1)
        return None

    all_pages = list(pages())
    only = set(arg("--pages").split(",")) if arg("--pages") else None
    chosen = [(k, d) for k, d in all_pages if only is None or k in only]
    record = {"leg": leg, "engine": engine, "device": device, "pages": {}, "docs": {}}

    # Warm-up: loads models (and, for a remote engine, its service's models), untimed.
    warm_key, warm_data = next((k, d) for k, d in all_pages if not k.startswith("V16"))
    t = time.perf_counter()
    try:
        _, produced, fell_back = convert(warm_data)
        record["warmup"] = {"page": warm_key, "seconds": round(time.perf_counter() - t, 2),
                            "engine": produced, "fell_back": fell_back}
    except Exception as exc:
        record["warmup"] = {"page": warm_key, "error": f"{type(exc).__name__}: {exc}"}
        traceback.print_exc()
    print(json.dumps({"leg": leg, "warmup": record["warmup"]}), flush=True)

    runs = {}
    if "--no-pages" not in sys.argv:
        for key, data in chosen:
            gpu_reset()
            t = time.perf_counter()
            try:
                md, produced, fell_back = convert(data)
                error = "fell back to the local parser" if fell_back else None
            except Exception as exc:  # recorded, never fatal
                md, produced, fell_back, error = None, None, False, f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            seconds = round(time.perf_counter() - t, 2)
            runs[key] = {"seconds": seconds, "ok": error is None, "error": error, "engine": produced,
                         "fell_back": fell_back, "gpu_peak_mb": gpu_peak()}
            if md is not None:
                (out / f"{key}.md").write_text(md)
            print(f"{leg} {key}: {seconds} s via {produced}{' FELL BACK' if fell_back else ''}"
                  f"{' ERROR ' + error if error and not fell_back else ''}", flush=True)
            # Written after every page: a leg cut off by the kernel's time budget keeps what it did.
            (out / "runs.json").write_text(json.dumps(runs, indent=2, sort_keys=True) + "\n")

    for pdf in filter(None, (arg("--docs") or "").split(",")):
        path = ROOT / pdf
        data = path.read_bytes()
        n_pages = pymupdf.open(stream=data, filetype="pdf").page_count
        gpu_reset()
        t = time.perf_counter()
        try:
            md, produced, fell_back = convert(data)
            error = None
        except Exception as exc:
            md, produced, fell_back, error = None, None, False, f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        seconds = round(time.perf_counter() - t, 2)
        record["docs"][path.name] = {"pages": n_pages, "seconds": seconds, "s_per_page": round(seconds / n_pages, 2),
                                     "engine": produced, "fell_back": fell_back, "error": error,
                                     "chars": len(md) if md else 0, "gpu_peak_mb": gpu_peak()}
        if md is not None:
            (out / f"doc-{path.stem}.md").write_text(md)
        print(json.dumps({"leg": leg, "doc": path.name, **record["docs"][path.name]}), flush=True)

    record["pages"] = {"n": len(runs), "ok": sum(r["ok"] for r in runs.values()),
                       "seconds_total": round(sum(r["seconds"] for r in runs.values()), 1)}
    (out / "docs.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
