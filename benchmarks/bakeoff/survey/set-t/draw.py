"""Draw set T: a second, held-out structural gold set (roadmap 14 item 3.7b).

Set S has been looked at while building the layout-region pass, so the pass is
judged again on pages nobody has annotated for structure or inspected layout
output for. Candidates: scanned pages with a frozen blind text reference
(sets B-F, L), not in set S, not set A (its references were corrected from
Tesseract drafts, not blind), and not V2 p9 (its layout regions were inspected
in the CPU smoke test). Single pages only. Seed 20261110.

  10 PBR journal pages, by layout group: 3 two-column prose, 3 two-column + data,
     2 tables, 2 single column
   4 other publications (set E), one per publication group

  python benchmarks/bakeoff/survey/set-t/draw.py   -> prints the draw (also in draw.txt)
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
BAKEOFF = HERE.parent.parent
sys.path.insert(0, str(BAKEOFF))
import regions as R  # noqa: E402

SEED = 20261110
QUOTA_PBR = {"two-column prose": 3, "two-column + data": 3, "tables": 2, "single column": 2}
EXCLUDE = {"V2_p9"}


def main() -> None:
    in_s = {f"{k}_p{n}" for k, ns in R.SAMPLE_S.items() for n in ns}
    refs = {p.stem for p in (BAKEOFF / "references").glob("*.txt")}
    pbr: dict[str, list[str]] = {}
    for groups in (R.GROUPS_B, R.GROUPS_C, R.GROUPS_D, R.GROUPS_F, R.GROUPS_L):
        for g, pages in groups.items():
            pbr.setdefault(g, []).extend(p for p in pages if p in refs and p not in in_s and p not in EXCLUDE)
    rng = random.Random(SEED)
    draw = []
    for g, k in QUOTA_PBR.items():
        draw += [(g, p) for p in rng.sample(sorted(set(pbr[g])), k)]
    for g, pages in R.GROUPS_E.items():
        pool = sorted(p for p in pages if p in refs and p not in in_s and p not in EXCLUDE)
        draw.append((g, rng.choice(pool)))
    lines = [f"{p}\t{g}" for g, p in draw]
    (HERE / "draw.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
