"""Survey image-only pages to draw a new held-out set (the set B/C/D procedure).

For every page of the given PDFs, runs the default OCR path's Tesseract once and
records the word count, the share of tokens containing a digit, the number of
columns ADR-035's detector finds, and the first/last lines (to map scans to
printed page numbers and catch duplicate scans). Then assigns layout groups and
makes a seeded stratified draw:

  near-empty         < 150 words (covers, photo pages)  -- excluded
  tables             >= 30% numbers
  two-column prose   2+ columns, < 12% numbers
  two-column + data  2+ columns, 12-30% numbers
  single column      everything else

  python benchmarks/bakeoff/survey.py OUT_DIR KEY=PDF [KEY=PDF ...] --seed N --quota 3,3,4,2

Set D: KEY V5a = "Volume 5 Number 1.pdf", V5b = "Volume 5 Number 3.pdf", seed 20261107,
quota prose 3, tables 3, data 4, single 2. Outputs: survey/set-d-*.json.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from parsers import ocr, reading_order  # noqa: E402

GROUPS = ["two-column prose", "tables", "two-column + data", "single column"]


def survey(docs: dict[str, str]) -> dict:
    out = {}
    for key, path in docs.items():
        for i, page in enumerate(pymupdf.open(path)):
            tp = page.get_textpage_ocr(dpi=300, full=True, language="eng")
            lines = ocr._ocr_lines(page, tp)
            text = page.get_text(textpage=tp)
            toks = re.findall(r"[^\W_]+", text.lower())
            nums = [t for t in toks if any(c.isdigit() for c in t)]
            gutters = reading_order._choose_gutters(lines) if len(lines) >= reading_order.MIN_LINES else []
            kept = [ln for ln in text.strip().splitlines() if ln.strip()]
            out[f"{key}_p{i + 1}"] = {"words": len(toks), "num_share": round(len(nums) / max(1, len(toks)), 3),
                                      "columns": len(gutters) + 1, "first": kept[:2], "last": kept[-3:]}
            print(f"{key}_p{i + 1}", out[f"{key}_p{i + 1}"], flush=True)
    return out


def group(v: dict) -> str:
    if v["words"] < 150:
        return "near-empty"
    if v["num_share"] >= 0.30:
        return "tables"
    if v["columns"] >= 2:
        return "two-column prose" if v["num_share"] < 0.12 else "two-column + data"
    return "single column"


def main() -> None:
    args = [a for a in sys.argv[1:]]
    seed = int(args[args.index("--seed") + 1])
    quota = [int(q) for q in args[args.index("--quota") + 1].split(",")]
    out_dir = Path(args[0])
    docs = dict(a.split("=", 1) for a in args[1:] if "=" in a)
    data = survey(docs)
    groups: dict[str, list[str]] = {g: [] for g in GROUPS + ["near-empty"]}
    for k, v in data.items():
        groups[group(v)].append(k)
    rng = random.Random(seed)
    draw = {g: sorted(rng.sample(groups[g], q)) for g, q in zip(GROUPS, quota)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "survey.json").write_text(json.dumps(data, indent=1) + "\n")
    (out_dir / "draw.json").write_text(json.dumps({"groups": groups, "draw": draw, "seed": seed}, indent=1) + "\n")
    print(json.dumps(draw, indent=1))


if __name__ == "__main__":
    main()
