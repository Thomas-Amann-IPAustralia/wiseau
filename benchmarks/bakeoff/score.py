"""Score engine outputs against the references.

Both sides are normalised the same way: HTML tags, Markdown syntax and LaTeX
commands are removed, the text is NFKC-folded and lower-cased, and it is split
into runs of letters/digits ("63A-C" -> "63a", "c"; leader dots vanish).

Metrics per page:
  wer    word edit distance / reference length (reading order counts)
  bow_r  word recall    (multiset overlap / reference words)
  bow_p  word precision (multiset overlap / output words)
  num_r / num_p  the same, restricted to tokens containing a digit
Group figures are the mean over the group's pages.

  python benchmarks/bakeoff/score.py            # prints the tables, writes results.json
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import SAMPLE  # noqa: E402

ENGINES = ["pymupdf", "docling", "glm-ocr", "gemini-3.5-flash",
           "vote-pymupdf", "vote-docling", "vote-glm-ocr", "consensus-order", "consensus-words",
           "oracle-best-single"]
METRICS = ["wer", "bow_r", "bow_p", "num_r", "num_p"]


def tokens(text: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", text)                 # HTML (tables, <br>)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)             # LaTeX commands
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)    # image links
    text = unicodedata.normalize("NFKC", text).lower()
    return re.findall(r"[^\W_]+", text)


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def overlap(ref: list[str], hyp: list[str]) -> tuple[float, float]:
    common = sum((Counter(ref) & Counter(hyp)).values())
    return (common / len(ref) if ref else 1.0), (common / len(hyp) if hyp else 0.0)


def score_page(ref_text: str, hyp_text: str) -> dict[str, float]:
    ref, hyp = tokens(ref_text), tokens(hyp_text)
    rn = [t for t in ref if any(c.isdigit() for c in t)]
    hn = [t for t in hyp if any(c.isdigit() for c in t)]
    bow_r, bow_p = overlap(ref, hyp)
    num_r, num_p = overlap(rn, hn)
    return {"wer": edit_distance(ref, hyp) / len(ref), "bow_r": bow_r, "bow_p": bow_p,
            "num_r": num_r, "num_p": num_p}


def main() -> None:
    results: dict = {}
    for engine in ENGINES:
        out = HERE / "outputs" / engine
        if not out.exists():
            continue
        timings = json.loads((out / "timings.json").read_text()) if (out / "timings.json").exists() else {}
        for doc, nums in SAMPLE.items():
            for n in nums:
                key = f"{doc}_p{n}"
                hyp = out / f"{key}.md"
                if not hyp.exists():
                    continue
                s = score_page((HERE / "references" / f"{key}.txt").read_text(), hyp.read_text())
                s["seconds"] = timings.get(key)
                results.setdefault(engine, {})[key] = s
    (HERE / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    print("| Engine | Pages | " + " | ".join(METRICS) + " | s/page |")
    print("|---|---|" + "---|" * (len(METRICS) + 1))
    for engine, pages in results.items():
        for label, pred in (("digital", lambda k: k.startswith("V16")), ("scanned", lambda k: not k.startswith("V16"))):
            rows = [v for k, v in pages.items() if pred(k)]
            if not rows:
                continue
            means = [sum(r[m] for r in rows) / len(rows) for m in METRICS]
            secs = [r["seconds"] for r in rows if r["seconds"] is not None]
            sp = f"{sum(secs) / len(secs):.0f}" if secs else "-"
            print(f"| {engine} — {label} | {len(rows)} | " + " | ".join(f"{x:.3f}" for x in means) + f" | {sp} |")
    print()
    print("| Page | " + " | ".join(f"{e} wer / bow_r / num_r" for e in results) + " |")
    print("|---|" + "---|" * len(results))
    for doc, nums in SAMPLE.items():
        for n in nums:
            key = f"{doc}_p{n}"
            cells = []
            for e in results:
                r = results[e].get(key)
                cells.append("—" if r is None else f"{r['wer']:.3f} / {r['bow_r']:.3f} / {r['num_r']:.3f}")
            print(f"| {key} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
