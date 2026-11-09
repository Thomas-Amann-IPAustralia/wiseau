"""Paired comparison of engines against a control, page by page (roadmap 14 items 1.2, 1.3).

Reads results.json (run score.py first). For each engine, over the pages both it
and the control have, per set: mean wer and cer, the change from the control, and
how many pages got better, worse or stayed the same (a wer change under 0.0005 is
"same").

  python benchmarks/bakeoff/compare.py pre-ident pre-deskew pre-norm --sets a,b,c,d,l
  python benchmarks/bakeoff/compare.py rec-tesseract rec-PP-OCRv6_small_rec --pages   # also list changed pages

The first engine named is the control. ``--sets`` defaults to every set; set A's
born-digital V16 pages are left out unless ``--digital`` is given.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L  # noqa: E402

SETS = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F, "l": SAMPLE_L}
SAME = 0.0005


def keys(set_name: str, digital: bool) -> list[str]:
    return [f"{d}_p{n}" for d, ns in SETS[set_name].items() for n in ns if digital or d != "V16"]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = sys.argv[1:]
    sets = (flags[flags.index("--sets") + 1].split(",") if "--sets" in flags else list(SETS))
    args = [a for a in args if a != ",".join(sets)]
    control, engines = args[0], args[1:]
    digital = "--digital" in flags
    results = json.loads((HERE / "results.json").read_text())
    base = results[control]
    print(f"Control: {control}\n")
    print("| Engine | Set | Pages | wer | Δwer | cer | Δcer | better | worse | same |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    changed = []
    for engine in engines:
        pages = results.get(engine, {})
        total = []
        for s in sets + ["all"]:
            ks = [k for k in (sum((keys(x, digital) for x in sets), []) if s == "all" else keys(s, digital))
                  if k in pages and k in base]
            if not ks:
                continue
            d = [pages[k]["wer"] - base[k]["wer"] for k in ks]
            dc = [pages[k]["cer"] - base[k]["cer"] for k in ks]
            wer = sum(pages[k]["wer"] for k in ks) / len(ks)
            cer = sum(pages[k]["cer"] for k in ks) / len(ks)
            better = sum(x < -SAME for x in d)
            worse = sum(x > SAME for x in d)
            print(f"| {engine} | {s} | {len(ks)} | {wer:.3f} | {sum(d) / len(d):+.3f} | {cer:.3f} | "
                  f"{sum(dc) / len(dc):+.3f} | {better} | {worse} | {len(ks) - better - worse} |")
            if s == "all":
                total = ks
        changed += [(engine, k, base[k]["wer"], pages[k]["wer"]) for k in total
                    if abs(pages[k]["wer"] - base[k]["wer"]) > SAME]
    if "--pages" in flags:
        print("\n| Engine | Page | control wer | wer |\n|---|---|---|---|")
        for engine, k, b, w in sorted(changed, key=lambda r: (r[0], r[3] - r[2])):
            print(f"| {engine} | {k} | {b:.3f} | {w:.3f} |")


if __name__ == "__main__":
    main()
