"""Check set T's gold files against their frozen blind references, word for word.

Every gold file's words must equal its reference's (`score.tokens`) as a
multiset, as set S's do: the structure annotation may add Markdown and
annotation comments, and may reorder words where the ideal Markdown reads them
in another order (a table's wrapped cells, a column change), never change one. Run from anywhere:

  python benchmarks/bakeoff/structure-t/check.py
"""
from __future__ import annotations

import difflib
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import score  # noqa: E402
import structure  # noqa: E402


def main() -> int:
    bad = 0
    for path in sorted(HERE.glob("T*.md")):
        m = re.match(r"^T\d+_([A-Za-z0-9]+)_p(\d+)$", path.stem)
        ref = HERE.parent / "references" / f"{m.group(1)}_p{m.group(2)}.txt"
        gold = [t for b in structure.parse_gold(path) for t, _ in b.tokens]
        want = score.tokens(ref.read_text(encoding="utf-8"))
        if gold == want or Counter(gold) == Counter(want):
            print(f"{path.name}: ok ({len(gold)} words, {'same order' if gold == want else 'reordered'})")
            continue
        bad += 1
        print(f"{path.name}: MISMATCH ({len(gold)} vs {len(want)} words)")
        for op in difflib.SequenceMatcher(a=want, b=gold, autojunk=False).get_opcodes():
            if op[0] != "equal":
                print("   ", op[0], want[op[1]:op[2]][:12], "->", gold[op[3]:op[4]][:12])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
