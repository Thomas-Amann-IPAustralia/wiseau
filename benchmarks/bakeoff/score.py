"""Score engine outputs against the references.

Both sides are normalised the same way: HTML tags, Markdown syntax and LaTeX
commands are removed, the text is NFKC-folded and lower-cased, and it is split
into runs of letters/digits ("63A-C" -> "63a", "c"; leader dots vanish).

Metrics per page:
  wer    word edit distance / reference length (reading order counts)
  cer    character edit distance / reference length, over the same normalised
         words joined by single spaces (so it sees misread letters inside a word
         that wer counts as one whole wrong word)
  ord    line reading-order error: each reference line of 3+ words is located in
         the output by trigram voting, and ord is the edit distance between the
         reference order and the order the output puts them in, / lines located.
         0 = the located lines come in reference order. References carry lines,
         not blocks, so this is a line-level stand-in for block order until the
         structural gold set (roadmap 2.3) annotates blocks.
  bow_r  word recall    (multiset overlap / reference words)
  bow_p  word precision (multiset overlap / output words)
  num_r / num_p  the same, restricted to tokens containing a digit
  pun_r / pun_p  the same over punctuation characters exactly as printed (NFC, no
         folding: ’ and ' differ), after Markdown syntax is removed. This is what
         ADR-039 (the normalizer keeps printed punctuation) is judged on;
         pun_rf is the recall with ‘’“”–—… folded to ASCII, which separates
         "kept the mark" from "kept its printed form".
Run metrics (from outputs/<engine>/runs.json, written by run_engines.py; "-" for
runs made before it existed):
  s/page  mean seconds per page
  rss     peak resident memory of the run, MB (high-water mark, own process and
          largest child; an HTTP engine's server is not visible here)
  fail    share of attempted pages that raised; a failed page is scored as empty
Group figures are the mean over the group's pages.

  python benchmarks/bakeoff/score.py            # set A tables; writes results.json (both sets)
  python benchmarks/bakeoff/score.py --set b    # set B tables, overall and per layout group
  python benchmarks/bakeoff/score.py --set c    # the same for set C (held out, 2026-11-03)
  python benchmarks/bakeoff/score.py --set d    # the same for set D (held out, 2026-11-06)
  python benchmarks/bakeoff/score.py --set f    # the same for set F (held out, 2026-11-08)
  python benchmarks/bakeoff/score.py --set e    # set E (other publications: patents, WO)
  python benchmarks/bakeoff/score.py --set l    # set L (random audit of Acrobat-OCR'd PBR pages)
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Hashable, Sequence
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from regions import (GROUPS_B, GROUPS_C, GROUPS_D, GROUPS_E, GROUPS_F, GROUPS_L, SAMPLE, SAMPLE_B,  # noqa: E402
                     SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L)

ENGINES = ["pymupdf", "no-borders", "trust-layer", "hidden-layer", "forced-ocr", "tesseract-layout", "tesseract-adr037", "tesseract-adr035", "tesseract-unordered", "tesseract-psm3", "docling", "glm-ocr", "gemini-3.5-flash",
           "vote-pymupdf", "vote-docling", "vote-glm-ocr", "consensus-order", "consensus-words",
           "router-rule", "oracle-best-single",
           # roadmap 14 item 1.2 (preprocess.py) and 1.3 (ml_engines.py), 2026-11-09
           "pre-ident", "pre-deskew", "pre-norm", "pre-sauvola", "pre-denoise",
           "rec-tesseract", "rec-PP-OCRv6_small_rec", "rec-en_PP-OCRv5_mobile_rec",
           "wrec-PP-OCRv6_small_rec", "wrec-en_PP-OCRv5_mobile_rec", "tatr", "ppstructure-v3",
           # roadmap 14 item 3.7 (ADR-046), 2026-11-09
           "tables-tatr"]
METRICS = ["wer", "cer", "ord", "bow_r", "bow_p", "num_r", "num_p", "pun_r", "pun_p"]


def tokens(text: str) -> list[str]:
    text = re.sub(r"</?[A-Za-z][^<>\n]*>", " ", text)    # HTML tags (tables, <br>); not stray "<" ... ">" OCR specks
    text = re.sub(r"\\[a-zA-Z]+", " ", text)             # LaTeX commands
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)    # image links
    text = unicodedata.normalize("NFKC", text).lower()
    return re.findall(r"[^\W_]+", text)


def edit_distance(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """Exact Levenshtein distance (unit costs) between two sequences.

    Myers' bit-parallel algorithm (Hyyrö's formulation): the same answer as the
    textbook dynamic programme, in O(len(a) * len(b) / word) instead of
    O(len(a) * len(b)) Python steps, which is what makes character-level CER
    affordable on 5,000-character pages. Works on any hashable symbols, so it
    serves wer (words) and cer (characters) alike.
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    m = len(b)
    full = (1 << m) - 1
    top = 1 << (m - 1)
    peq: dict = {}
    for i, sym in enumerate(b):
        peq[sym] = peq.get(sym, 0) | (1 << i)
    pv, mv, score = full, 0, m
    for sym in a:
        eq = peq.get(sym, 0)
        xv = eq | mv
        xh = ((((eq & pv) + pv) & full) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & full
        mh = pv & xh
        if ph & top:
            score += 1
        elif mh & top:
            score -= 1
        ph = ((ph << 1) | 1) & full
        mh = (mh << 1) & full
        pv = (mh | ~(xv | ph)) & full
        mv = ph & xv
    return score


def _edit_distance_dp(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """The textbook dynamic programme; kept as the oracle `edit_distance` is checked against."""
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


# Markdown syntax that is not printed punctuation: table delimiter rows, cell
# pipes, heading hashes, list and quote markers, emphasis runs.
_MD_TABLE_DELIMITER = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*(?:[-*+]|>+)\s+", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(?<!\w)(\*{1,3}|_{1,3})(?=\S)|(?<=\S)(\*{1,3}|_{1,3})(?!\w)")
_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": "-", "\u2014": "-", "\u2026": "."})


def punctuation(text: str) -> list[str]:
    """Punctuation characters as printed (Unicode category P*), Markdown syntax removed."""
    text = re.sub(r"</?[A-Za-z][^<>\n]*>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = _MD_TABLE_DELIMITER.sub(" ", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_LIST.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = text.replace("|", " ")
    text = unicodedata.normalize("NFC", text)
    return [c for c in text if unicodedata.category(c).startswith("P")]


def _trigrams(words: list[str]) -> list[tuple[str, str, str]]:
    return [tuple(words[i:i + 3]) for i in range(len(words) - 2)]


def line_order_error(ref_text: str, hyp: list[str]) -> tuple[float | None, int]:
    """(ord, lines located). Each reference line of 3+ words is placed where the
    most of its trigrams agree it starts in the output; a line is located when at
    least half its trigrams agree. ord is the edit distance between the located
    lines in reference order and in output order, over the number located."""
    index: dict[tuple[str, str, str], list[int]] = {}
    for pos, gram in enumerate(_trigrams(hyp)):
        index.setdefault(gram, []).append(pos)
    placed: list[tuple[int, int]] = []           # (output start, reference line number)
    for number, line in enumerate(ref_text.splitlines()):
        grams = _trigrams(tokens(line))
        if not grams:
            continue
        votes: Counter = Counter()
        for offset, gram in enumerate(grams):
            for pos in index.get(gram, ()):
                votes[pos - offset] += 1
        if not votes:
            continue
        start, count = min(votes.items(), key=lambda item: (-item[1], item[0]))
        if count * 2 >= len(grams):
            placed.append((start, number))
    if len(placed) < 2:
        return None, len(placed)
    in_ref = [number for _, number in placed]
    in_hyp = [number for _, number in sorted(placed)]
    return edit_distance(in_ref, in_hyp) / len(placed), len(placed)


def score_page(ref_text: str, hyp_text: str) -> dict[str, float | None]:
    ref, hyp = tokens(ref_text), tokens(hyp_text)
    rn = [t for t in ref if any(c.isdigit() for c in t)]
    hn = [t for t in hyp if any(c.isdigit() for c in t)]
    bow_r, bow_p = overlap(ref, hyp)
    num_r, num_p = overlap(rn, hn)
    rc, hc = " ".join(ref), " ".join(hyp)
    rp, hp = punctuation(ref_text), punctuation(hyp_text)
    pun_r, pun_p = overlap(rp, hp)
    pun_rf, _ = overlap([c.translate(_FOLD) for c in rp], [c.translate(_FOLD) for c in hp])
    order, located = line_order_error(ref_text, hyp)
    return {"wer": edit_distance(ref, hyp) / len(ref), "cer": edit_distance(rc, hc) / len(rc),
            "ord": order, "ord_lines": located, "bow_r": bow_r, "bow_p": bow_p,
            "num_r": num_r, "num_p": num_p, "pun_r": pun_r, "pun_p": pun_p, "pun_rf": pun_rf}


def _runs(out: Path) -> dict:
    """Per-page run records: runs.json if the run wrote one, else timings.json alone."""
    if (out / "runs.json").exists():
        return json.loads((out / "runs.json").read_text())
    timings = json.loads((out / "timings.json").read_text()) if (out / "timings.json").exists() else {}
    return {key: {"seconds": secs} for key, secs in timings.items()}


def main() -> None:
    results: dict = {}
    for engine in ENGINES:
        out = HERE / "outputs" / engine
        if not out.exists():
            continue
        runs = _runs(out)
        for doc, nums in list(SAMPLE.items()) + list(SAMPLE_B.items()) + list(SAMPLE_C.items()) + list(SAMPLE_D.items()) + list(SAMPLE_F.items()) + list(SAMPLE_E.items()) + list(SAMPLE_L.items()):
            for n in nums:
                key = f"{doc}_p{n}"
                hyp = out / f"{key}.md"
                run = runs.get(key, {})
                failed = run.get("ok") is False
                if not hyp.exists() and not failed:
                    continue
                text = "" if failed else hyp.read_text()
                s = score_page((HERE / "references" / f"{key}.txt").read_text(), text)
                s["seconds"] = run.get("seconds")
                s["rss_mb"] = run.get("rss_mb")
                s["failed"] = failed if "ok" in run else None
                results.setdefault(engine, {})[key] = s
    (HERE / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    chosen = sys.argv[sys.argv.index("--set") + 1].lower() if "--set" in sys.argv else "a"
    if chosen in {"b", "c", "d", "e", "f", "l"}:
        print_set(results, *({"b": (SAMPLE_B, GROUPS_B), "c": (SAMPLE_C, GROUPS_C), "d": (SAMPLE_D, GROUPS_D),
                              "e": (SAMPLE_E, GROUPS_E), "f": (SAMPLE_F, GROUPS_F),
                              "l": (SAMPLE_L, GROUPS_L)}[chosen]))
        return

    print("| Engine | Pages | " + " | ".join(METRICS) + " | " + " | ".join(RUN_COLUMNS) + " |")
    print("|---|---|" + "---|" * (len(METRICS) + len(RUN_COLUMNS)))
    for engine, pages in results.items():
        for label, pred in (("digital", lambda k: k.startswith("V16")), ("scanned", lambda k: not k.startswith("V16"))):
            rows = [v for k, v in pages.items() if pred(k)]
            if not rows:
                continue
            print(f"| {engine} — {label} | {len(rows)} | " + _mean_row(rows))
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


RUN_COLUMNS = ["s/page", "rss", "fail"]


def _mean(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _mean_row(rows: list[dict]) -> str:
    """Metric means over the rows, then s/page, peak RSS and failure rate.

    A metric a page cannot have (ord on a page with under two locatable lines) is
    left out of that metric's mean rather than counted as zero."""
    cells = []
    for metric in METRICS:
        value = _mean([r.get(metric) for r in rows])
        cells.append("-" if value is None else f"{value:.3f}")
    secs = _mean([r.get("seconds") for r in rows])
    cells.append("-" if secs is None else f"{secs:.0f}")
    rss = [r["rss_mb"] for r in rows if r.get("rss_mb") is not None]
    cells.append(f"{max(rss):.0f}" if rss else "-")
    flags = [r["failed"] for r in rows if r.get("failed") is not None]
    cells.append(f"{sum(flags) / len(flags):.2f}" if flags else "-")
    return " | ".join(cells) + " |"


def print_set(results: dict, sample: dict, groups: dict) -> None:
    keys = [f"{d}_p{n}" for d, ns in sample.items() for n in ns]
    complete = {e: p for e, p in results.items() if all(k in p for k in keys)}
    print("| Engine | Pages | " + " | ".join(METRICS) + " | " + " | ".join(RUN_COLUMNS) + " |")
    print("|---|---|" + "---|" * (len(METRICS) + len(RUN_COLUMNS)))
    for engine, pages in complete.items():
        print(f"| {engine} | {len(keys)} | " + _mean_row([pages[k] for k in keys]))
    for group, members in groups.items():
        print(f"\n**{group}** ({', '.join(members)})\n")
        print("| Engine | " + " | ".join(METRICS) + " | " + " | ".join(RUN_COLUMNS) + " |")
        print("|---|" + "---|" * (len(METRICS) + len(RUN_COLUMNS)))
        for engine, pages in complete.items():
            print(f"| {engine} | " + _mean_row([pages[k] for k in members]))
    print("\n| Page | " + " | ".join(f"{e} wer / bow_r / num_r" for e in complete) + " |")
    print("|---|" + "---|" * len(complete))
    for k in keys:
        cells = [f"{complete[e][k]['wer']:.3f} / {complete[e][k]['bow_r']:.3f} / {complete[e][k]['num_r']:.3f}"
                 for e in complete]
        print(f"| {k} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
