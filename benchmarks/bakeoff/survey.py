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
        [--exclude KEY_pN,KEY_pN,...] [--split-dpi]

Each page also records `dpi`, the native resolution of its largest image (pixels
per inch of the page it covers), and `hidden_layer_words`, the words in its own
text layer (an invisible OCR layer on a scan; 0 for an image-only page). Pages are
OCR'd from their images either way (`full=True`), never read from that layer.

`--exclude` drops pages before grouping (test charts, duplicate scans, pages an
earlier set used). `--split-dpi` splits each group's quota between low-resolution
pages (< 250 dpi) and the rest, in proportion to how many of each the group has
(at least one of each when the quota is 2+ and both exist), so a held-out set
covers both when a PDF mixes them.

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


LOW_DPI = 250


def native_dpi(page: pymupdf.Page) -> int | None:
    """Resolution of the page's largest image, in pixels per inch of page it covers."""
    best = None
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        area = (x1 - x0) * (y1 - y0)
        if area > 0 and (best is None or area > best[0]):
            best = (area, info["width"] / ((x1 - x0) / 72))
    return round(best[1]) if best else None


def survey(docs: dict[str, str]) -> dict:
    out = {}
    for key, path in docs.items():
        for i, page in enumerate(pymupdf.open(path)):
            hidden = len(re.findall(r"[^\W_]+", page.get_text()))
            dpi = native_dpi(page)
            tp = page.get_textpage_ocr(dpi=300, full=True, language="eng")
            lines = ocr._ocr_lines(page, tp)
            text = page.get_text(textpage=tp)
            toks = re.findall(r"[^\W_]+", text.lower())
            nums = [t for t in toks if any(c.isdigit() for c in t)]
            gutters = reading_order._choose_gutters(lines) if len(lines) >= reading_order.MIN_LINES else []
            kept = [ln for ln in text.strip().splitlines() if ln.strip()]
            out[f"{key}_p{i + 1}"] = {"words": len(toks), "num_share": round(len(nums) / max(1, len(toks)), 3),
                                      "columns": len(gutters) + 1, "dpi": dpi, "hidden_layer_words": hidden,
                                      "first": kept[:2], "last": kept[-3:]}
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


def split_quota(q: int, n_low: int, n_high: int) -> tuple[int, int]:
    """(low, high) shares of a quota of q, proportional, at least one each when possible."""
    n = n_low + n_high
    if n == 0 or q == 0:
        return 0, 0
    low = round(q * n_low / n)
    if q >= 2 and n_low and n_high:
        low = min(max(low, 1), q - 1)
    low = min(low, n_low)
    return low, min(q - low, n_high)


def draw_group(rng: random.Random, members: list[str], q: int, data: dict, by_dpi: bool) -> list[str]:
    if not by_dpi:
        return sorted(rng.sample(members, q))
    low = [k for k in members if (data[k].get("dpi") or 300) < LOW_DPI]
    high = [k for k in members if k not in low]
    q_low, q_high = split_quota(q, len(low), len(high))
    return sorted(rng.sample(low, q_low) + rng.sample(high, q_high))


def main() -> None:
    args = [a for a in sys.argv[1:]]
    seed = int(args[args.index("--seed") + 1])
    quota = [int(q) for q in args[args.index("--quota") + 1].split(",")]
    exclude = set(args[args.index("--exclude") + 1].split(",")) if "--exclude" in args else set()
    by_dpi = "--split-dpi" in args
    out_dir = Path(args[0])
    docs = dict(a.split("=", 1) for a in args[1:] if "=" in a)
    survey_file = out_dir / "survey.json"
    # Re-drawing with a new exclusion list must not need a second OCR pass.
    data = json.loads(survey_file.read_text()) if survey_file.exists() and "--resurvey" not in args else survey(docs)
    groups: dict[str, list[str]] = {g: [] for g in GROUPS + ["near-empty", "excluded"]}
    for k, v in data.items():
        groups["excluded" if k in exclude else group(v)].append(k)
    rng = random.Random(seed)
    draw = {g: draw_group(rng, groups[g], q, data, by_dpi) for g, q in zip(GROUPS, quota)}
    out_dir.mkdir(parents=True, exist_ok=True)
    survey_file.write_text(json.dumps(data, indent=1) + "\n")
    (out_dir / "draw.json").write_text(json.dumps({"groups": groups, "draw": draw, "seed": seed,
                                                   "quota": quota, "split_dpi": by_dpi,
                                                   "exclude": sorted(exclude)}, indent=1) + "\n")
    print(json.dumps(draw, indent=1))


if __name__ == "__main__":
    main()
