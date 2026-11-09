"""Choose and check the Table Transformer pass's settings from one cached run (ADR-046).

Phase 1 (`cache`) runs the default path up to psm 3's reading and Table
Transformer's detection and structure once per scored scan, and pickles them.
Phase 2 (`eval`) rebuilds the tables from that cache under each setting (row
splitting, spanning cells, the tabularity check's threshold) and scores:

* words: wer and ord against the references, by set, development sets (A-D, L)
  apart from the held-out ones (E, F); per page, against the default path;
* structure: set S's documents with `structure.py` (blocks, types, tables).

No model runs in phase 2, so a grid of settings costs seconds. The settings it
picks are then confirmed by an end-to-end run (`run_engines.py tables-tatr`).

  python benchmarks/bakeoff/tatr_eval.py cache DIR a b      # one worker per group of sets
  python benchmarks/bakeoff/tatr_eval.py eval DIR [--pages]
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from itertools import product
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))

import pymupdf  # noqa: E402
import structure  # noqa: E402
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L  # noqa: E402
from score import score_page  # noqa: E402

from parsers import serializer, tables  # noqa: E402
from parsers.cleaner import clean_markdown  # noqa: E402
from parsers.file_parser import _page_needs_ocr  # noqa: E402

SETS = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F, "l": SAMPLE_L}
DEV, HELD = ("a", "b", "c", "d", "l"), ("e", "f")


# The cache is pickled: it is written and read only by this script, in a local
# directory the caller names; never point `eval` at a cache from anywhere else.
def cache(out: Path, sets: list[str]) -> None:
    from tatr_regions import page_layout

    out.mkdir(parents=True, exist_ok=True)
    for s in sets:
        for doc_key, nums in SETS[s].items():
            doc = pymupdf.open(ROOT / DOCS[doc_key])
            for n in nums:
                key = f"{doc_key}_p{n}"
                one = pymupdf.open()
                one.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
                if not _page_needs_ocr(one[0]):
                    continue
                _keep, page, layout, words = page_layout(one[0])
                t = time.perf_counter()
                dets = tables.detect(page) if layout is not None else []
                seconds = time.perf_counter() - t
                with open(out / f"{key}.pkl", "wb") as f:
                    pickle.dump({"set": s, "layout": layout, "words": words, "detections": dets,
                                 "seconds": seconds}, f)
                print(key, f"{seconds:.1f}s", len(dets), flush=True)


def page_markdown(entry: dict, settings: dict | None) -> str:
    """One cached page as the default path's Markdown, with tables under ``settings`` (None: off)."""
    layout = entry["layout"]
    if layout is None:
        return None
    if settings is not None:
        tables.ROW_SPLIT, tables.SPANS = settings["rows"], settings["spans"]
        tables.MAX_WORDS_PER_CELL = settings["max_wpc"]
        present = [ln for el in layout for ln in el.lines]
        layout = tables.apply(layout, tables.regions_from(entry["detections"], entry["words"], present), entry["words"])
    return clean_markdown(serializer.render_elements(layout))


def evaluate(cache_dir: Path, per_page: bool) -> None:
    entries = {p.stem: pickle.load(open(p, "rb")) for p in sorted(cache_dir.glob("*.pkl"))}
    refs = {k: (HERE / "references" / f"{k}.txt").read_text() for k in entries}
    default_md = {k: (HERE / "outputs" / "pymupdf" / f"{k}.md").read_text() for k in entries}
    base = {k: score_page(refs[k], default_md[k]) for k in entries}
    # The cache reproduces the default path exactly when the pass is off.
    off = {k: page_markdown(e, None) for k, e in entries.items()}
    same = sum(1 for k in entries if off[k] is None or off[k] == default_md[k])
    print(f"cache check: {same}/{len(entries)} pages reproduce outputs/pymupdf with the pass off "
          f"({sum(1 for k in off if off[k] is None)} use the guard text, which the pass never touches)")
    docs = structure.gold_documents()

    def struct_scores(md_for):
        rows = {}
        for name, key, pages, path in docs:
            parts = []
            for n in pages:
                k = f"{key}_p{n}"
                md = md_for(k) if k in entries else None
                if md is None:
                    f = HERE / "outputs" / "pymupdf" / f"{k}.md"
                    if not f.exists():
                        break
                    md = f.read_text()
                parts.append((n, md))
            else:
                rows[name] = structure.score_document(structure.parse_gold(path), structure.parse_output(parts))
        return rows

    grid = [dict(rows=r, spans=sp, max_wpc=m)
            for r, sp, m in product(("none", "aligned", "lines"), (True, False), (2.5, 3.0, 3.5, 4.0, 5.0, 1e9))]
    variants = [("default", None)] + [(f"rows={g['rows']} spans={int(g['spans'])} wpc<={g['max_wpc']:g}", g) for g in grid]
    print(f"{'variant':<36} {'dev Δwer':>9} {'held Δwer':>9} {'dev b/w':>8} {'held b/w':>8} "
          f"{'S teds':>7} {'S teds_s':>8} {'S blk_f':>7} {'S typ_f':>7}")
    results = {}
    for name, g in variants:
        md = {k: (default_md[k] if g is None else (page_markdown(e, g) or default_md[k])) for k, e in entries.items()}
        sc = {k: score_page(refs[k], md[k]) for k in entries}
        d = {k: sc[k]["wer"] - base[k]["wer"] for k in entries}
        row = {}
        for label, group in (("dev", DEV), ("held", HELD)):
            ks = [k for k in entries if entries[k]["set"] in group]
            row[label] = sum(d[k] for k in ks) / len(ks)
            row[label + "_bw"] = (sum(d[k] < -1e-9 for k in ks), sum(d[k] > 1e-9 for k in ks))
        st = struct_scores(lambda k: md[k])
        for m in ("teds", "teds_s", "blk_f", "typ_f"):
            row["S_" + m] = structure._mean([st[x][m] for x in st])
        results[name] = {"row": row, "pages": {k: round(d[k], 4) for k in entries}}
        print(f"{name:<36} {row['dev']:>+9.4f} {row['held']:>+9.4f} {'%d/%d' % row['dev_bw']:>8} "
              f"{'%d/%d' % row['held_bw']:>8} {row['S_teds']:>7.3f} {row['S_teds_s']:>8.3f} "
              f"{row['S_blk_f']:>7.3f} {row['S_typ_f']:>7.3f}", flush=True)
    (cache_dir / "eval.json").write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "cache":
        cache(Path(sys.argv[2]), sys.argv[3:])
    else:
        evaluate(Path(sys.argv[2]), "--pages" in sys.argv)
