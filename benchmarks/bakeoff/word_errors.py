"""List every word-level error an engine made on a set, against the blind references.

`score.py` says *how much* an engine got wrong; this says *what*. Each page's
reference and output are tokenised exactly as `score.py` does (lowercased words and
numbers, punctuation dropped), aligned by `difflib.SequenceMatcher`, and every
non-matching stretch is printed as one of:

  sub  reference words -> output words     (a misreading)
  del  reference words -> (missing)        (text the output lost)
  ins  (extra)          -> output words    (text the output invented or duplicated)

followed by per-page counts and the set's totals. Word order differences show up as
a `del` in one place and an `ins` in another, so read `ord` from `score.py` for order.

  python benchmarks/bakeoff/word_errors.py ENGINE --set l [--max 40] [--summary]

Used for roadmap 14 item 0.2, to catalogue what a hidden OCR text layer gets wrong
(`hidden-layer`) next to what fresh OCR gets wrong (`forced-ocr`) on the same pages.
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L  # noqa: E402
from score import tokens  # noqa: E402

SETS = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F, "l": SAMPLE_L}


def errors(ref: list[str], hyp: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """(kind, reference words, output words) for every non-matching stretch, in reference order."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
        if tag == "replace":
            out.append(("sub", ref[i1:i2], hyp[j1:j2]))
        elif tag == "delete":
            out.append(("del", ref[i1:i2], []))
        elif tag == "insert":
            out.append(("ins", [], hyp[j1:j2]))
    return out


def counts(errs: list[tuple[str, list[str], list[str]]]) -> dict[str, int]:
    """Reference words misread, lost, and output words inserted."""
    return {"sub": sum(len(r) for k, r, _ in errs if k == "sub"),
            "del": sum(len(r) for k, r, _ in errs if k == "del"),
            "ins": sum(len(h) for k, _, h in errs if k == "ins")}


def main() -> None:
    args = sys.argv[1:]
    engine = args[0]
    chosen = args[args.index("--set") + 1].lower() if "--set" in args else "a"
    limit = int(args[args.index("--max") + 1]) if "--max" in args else 40
    summary = "--summary" in args
    total = {"words": 0, "sub": 0, "del": 0, "ins": 0}
    for doc, nums in SETS[chosen].items():
        for n in nums:
            key = f"{doc}_p{n}"
            hyp_file = HERE / "outputs" / engine / f"{key}.md"
            ref = tokens((HERE / "references" / f"{key}.txt").read_text())
            hyp = tokens(hyp_file.read_text()) if hyp_file.exists() else []
            errs = errors(ref, hyp)
            c = counts(errs)
            total["words"] += len(ref)
            for k in c:
                total[k] += c[k]
            print(f"## {key}: {len(ref)} reference words; misread {c['sub']}, lost {c['del']}, "
                  f"inserted {c['ins']}" + ("" if hyp_file.exists() else " (no output)"))
            if summary:
                continue
            for kind, r, h in errs[:limit]:
                shown_r = " ".join(r[:12]) + (" ..." if len(r) > 12 else "") or "(extra)"
                shown_h = " ".join(h[:12]) + (" ..." if len(h) > 12 else "") or "(missing)"
                print(f"  {kind}  {shown_r}  ->  {shown_h}")
            if len(errs) > limit:
                print(f"  ... {len(errs) - limit} more")
    w = max(1, total["words"])
    print(f"\nTOTAL {engine} set {chosen}: {total['words']} reference words; misread {total['sub']} "
          f"({total['sub'] / w:.1%}), lost {total['del']} ({total['del'] / w:.1%}), inserted {total['ins']}")


if __name__ == "__main__":
    main()
