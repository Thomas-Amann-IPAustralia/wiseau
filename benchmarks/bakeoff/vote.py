"""Word-level majority voting over the three self-hosted engines (ROVER-style).

The question this answers: can the offline engines we already run beat each of
them alone, just by voting, before any money is spent on labels or training?

How it works, per page:
  1. Tokenise every engine's output with the scorer's own `tokens()`, so the vote
     happens on exactly the units the scorer counts.
  2. Pick one engine as the *pivot* and align each of the other two to it with a
     word-level edit-distance alignment.
  3. Every pivot word is a slot. Each engine votes for what it has there: the
     pivot its own word, the others the word aligned to it, or "nothing" if they
     deleted it. The majority wins; a three-way split keeps the pivot's word.
  4. Words the pivot lacks (insertions in the other two) are kept only where
     both other engines inserted the same word in the same gap, i.e. 2 of 3.

Ties fall to the pivot, so the pivot choice acts as the engine weighting. Every
engine is tried as pivot. The output is a plain token sequence, not Markdown:
this measures whether voting finds the right *words*, not how to rebuild layout.

Gemini is deliberately excluded. It is the target to beat, not a voter.

Also written: `oracle-best-single`, which takes the best single engine's output
for each page as judged *against the reference*. It is not a usable method,
because it cheats, but it is the ceiling of a perfect per-page router (option C).

And two *usable* routers that need no reference, `consensus-order` and
`consensus-words`: each page takes the engine that agrees most with the other two,
measured by word edit distance (order counts) or by word recall (order ignored).

  python benchmarks/bakeoff/vote.py      # writes outputs/vote-*/ and outputs/oracle-best-single/
  python benchmarks/bakeoff/score.py     # scores them alongside the engines
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import SAMPLE  # noqa: E402
from score import edit_distance, overlap, score_page, tokens  # noqa: E402

VOTERS = ["pymupdf", "docling", "glm-ocr"]
GAP = ""  # the "nothing here" vote


def align(pivot: list[str], other: list[str]) -> tuple[list[str], list[list[str]]]:
    """Align `other` to `pivot` by word edit distance.

    Returns (slot, ins): slot[i] is the word of `other` aligned to pivot[i]
    (GAP if `other` deleted it); ins[g] is what `other` inserted in gap g, the gap
    before pivot[g] (g == len(pivot) is the gap after the last word).
    """
    n, m = len(pivot), len(other)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        row, prev, x = d[i], d[i - 1], pivot[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (x != other[j - 1]))
    slot = [GAP] * n
    ins: list[list[str]] = [[] for _ in range(n + 1)]
    i, j = n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (pivot[i - 1] != other[j - 1]):
            slot[i - 1] = other[j - 1]
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            i -= 1                                   # other deleted pivot[i-1]
        else:
            ins[i].insert(0, other[j - 1])           # other inserted before pivot[i]
            j -= 1
    return slot, ins


def common(a: list[str], b: list[str]) -> list[str]:
    """The words two insertion runs agree on, in order (their LCS)."""
    if not a or not b:
        return []
    slot, _ = align(a, b)
    return [x for x, y in zip(a, slot) if x == y]


def vote(pivot: list[str], others: list[list[str]]) -> list[str]:
    (s1, i1), (s2, i2) = (align(pivot, o) for o in others)
    out: list[str] = []
    for g, word in enumerate(pivot):
        out += common(i1[g], i2[g])
        a, b = s1[g], s2[g]
        winner = a if a == b else word               # 2 of 3 beat the pivot; else pivot
        if winner != GAP:
            out.append(winner)
    out += common(i1[len(pivot)], i2[len(pivot)])
    return out


def main() -> None:
    outputs = HERE / "outputs"
    timings = {e: json.loads((outputs / e / "timings.json").read_text()) for e in VOTERS}
    keys = [f"{doc}_p{n}" for doc, nums in SAMPLE.items() for n in nums]
    toks = {e: {k: tokens((outputs / e / f"{k}.md").read_text()) for k in keys} for e in VOTERS}
    # Voting needs every engine on every page, run one after another.
    total = {k: round(sum(timings[e][k] for e in VOTERS), 1) for k in keys}

    for pivot in VOTERS:
        rest = [e for e in VOTERS if e != pivot]
        out = outputs / f"vote-{pivot}"
        out.mkdir(exist_ok=True)
        for k in keys:
            words = vote(toks[pivot][k], [toks[e][k] for e in rest])
            (out / f"{k}.md").write_text(" ".join(words) + "\n")
        (out / "timings.json").write_text(json.dumps(total, indent=2) + "\n")

    out = outputs / "oracle-best-single"
    out.mkdir(exist_ok=True)
    picks = {}
    for k in keys:
        ref = (HERE / "references" / f"{k}.txt").read_text()
        best = min(VOTERS, key=lambda e: score_page(ref, (outputs / e / f"{k}.md").read_text())["wer"])
        picks[k] = best
        (out / f"{k}.md").write_text((outputs / best / f"{k}.md").read_text())
    (out / "timings.json").write_text(json.dumps({k: timings[picks[k]][k] for k in keys}, indent=2) + "\n")
    (out / "picks.json").write_text(json.dumps(picks, indent=2) + "\n")
    print("oracle picks:", picks)

    def disagreement_order(e: str, k: str) -> float:
        return sum(edit_distance(toks[e][k], toks[o][k]) / max(len(toks[o][k]), 1) for o in VOTERS if o != e)

    def disagreement_words(e: str, k: str) -> float:
        return sum(1 - overlap(toks[o][k], toks[e][k])[0] for o in VOTERS if o != e)

    for name, cost in (("consensus-order", disagreement_order), ("consensus-words", disagreement_words)):
        out = outputs / name
        out.mkdir(exist_ok=True)
        chosen = {k: min(VOTERS, key=lambda e: cost(e, k)) for k in keys}
        for k in keys:
            (out / f"{k}.md").write_text((outputs / chosen[k] / f"{k}.md").read_text())
        # Every engine must run before the page can be judged.
        (out / "timings.json").write_text(json.dumps(total, indent=2) + "\n")
        (out / "picks.json").write_text(json.dumps(chosen, indent=2) + "\n")
        print(f"{name} picks:", chosen)


if __name__ == "__main__":
    main()
