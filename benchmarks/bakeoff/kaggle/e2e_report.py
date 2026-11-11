"""Report the end-to-end GPU benchmark (e2e_kernel.py) from its fetched outputs.

  python benchmarks/bakeoff/kaggle/e2e_report.py benchmarks/bakeoff/outputs/kaggle-e2e-<date>

For every leg: pages converted, how many fell back or failed, warm-up (cold
start), mean and median seconds a page for scanned and born-digital pages,
whole-document seconds, and accuracy against the references (score.py's
`score_page`: wer, cer, bow_r, num_r) over the pages the leg converted. Then:
which image-path pages differ between CPU and GPU (and between two GPU runs),
and the committed CPU runs of docling and GLM-OCR, scored the same way, for
reference. No external service is called.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
sys.path.insert(0, str(BENCH))
from score import score_page  # noqa: E402

METRICS = ("wer", "cer", "bow_r", "num_r")


def digital(key: str) -> bool:
    return key.startswith("V16")


def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def stats(values: list[float]) -> tuple:
    return (statistics.mean(values), statistics.median(values), max(values)) if values else (None, None, None)


def quality(directory: Path, keys: list[str]) -> dict:
    rows = []
    for key in keys:
        hyp = directory / f"{key}.md"
        if hyp.exists():
            rows.append(score_page((BENCH / "references" / f"{key}.txt").read_text(), hyp.read_text()))
    return {m: (statistics.mean(r[m] for r in rows) if rows else None) for m in METRICS} | {"n": len(rows)}


def main() -> None:
    root = Path(sys.argv[1])
    legs = sorted(p for p in root.iterdir() if p.is_dir() and (p / "runs.json").exists())
    print("| Leg | pages ok | fell back / failed | warm-up s | scans mean / median / max s | digital mean s "
          "| whole docs (s, s/page) | wer | cer | bow_r | num_r |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    report = {}
    for leg in legs:
        runs = json.loads((leg / "runs.json").read_text())
        docs = json.loads((leg / "docs.json").read_text()) if (leg / "docs.json").exists() else {}
        ok = {k: r for k, r in runs.items() if r["ok"]}
        bad = {k: r for k, r in runs.items() if not r["ok"]}
        scans = stats([r["seconds"] for k, r in ok.items() if not digital(k)])
        dig = stats([r["seconds"] for k, r in ok.items() if digital(k)])
        q = quality(leg, sorted(ok))
        whole = "; ".join(f"{name.split('.')[0]}: {d['seconds']:.0f}, {d['s_per_page']:.1f}"
                          + (" (fell back)" if d.get("fell_back") else "") + (" ERROR" if d.get("error") else "")
                          for name, d in docs.get("docs", {}).items()) or "-"
        warm = docs.get("warmup", {}).get("seconds")
        print(f"| {leg.name} | {len(ok)}/{len(runs)} | {sum(r['fell_back'] for r in bad.values())} / "
              f"{sum(not r['fell_back'] for r in bad.values())} | {fmt(warm)} | "
              f"{fmt(scans[0])} / {fmt(scans[1])} / {fmt(scans[2])} | {fmt(dig[0])} | {whole} | "
              + " | ".join(fmt(q[m], 3) for m in METRICS) + " |")
        report[leg.name] = {"ok": len(ok), "pages": len(runs), "scans": scans, "digital": dig, "quality": q,
                            "docs": docs.get("docs", {}), "warmup": warm,
                            "errors": {k: r["error"] for k, r in bad.items()}}

    print("\nImage path, CPU vs GPU (Markdown bytes):")
    base = root / "image-cpu1"
    for other in ("image-cpu4", "image-gpu", "image-gpu-repeat"):
        o = root / other
        if not (base.exists() and o.exists()):
            continue
        keys = sorted(p.name for p in base.glob("*.md") if (o / p.name).exists())
        diff = [k for k in keys if (base / k).read_bytes() != (o / k).read_bytes()]
        print(f"- image-cpu1 vs {other}: {len(keys) - len(diff)}/{len(keys)} files identical"
              + (f"; differ: {', '.join(diff)}" if diff else ""))
    g, r = root / "image-gpu", root / "image-gpu-repeat"
    if g.exists() and r.exists():
        keys = sorted(p.name for p in r.glob("*.md") if (g / p.name).exists())
        diff = [k for k in keys if (g / k).read_bytes() != (r / k).read_bytes()]
        print(f"- image-gpu vs image-gpu-repeat: {len(keys) - len(diff)}/{len(keys)} identical"
              + (f"; differ: {', '.join(diff)}" if diff else ""))

    print("\nCommitted CPU runs (other machine, 2026-11-01..03), same pages, for reference:")
    keys = sorted({k for leg in legs for k in json.loads((leg / "runs.json").read_text())})
    for engine in ("docling", "glm-ocr", "pymupdf"):
        d = BENCH / "outputs" / engine
        q = quality(d, keys)
        t = json.loads((d / "timings.json").read_text()) if (d / "timings.json").exists() else {}
        sc = stats([t[k] for k in keys if k in t and not digital(k)])
        print(f"- {engine}: {q['n']} pages, wer {fmt(q['wer'], 3)}, bow_r {fmt(q['bow_r'], 3)}; "
              f"scans {fmt(sc[0])} s mean, {fmt(sc[1])} median")
    (root / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    main()
