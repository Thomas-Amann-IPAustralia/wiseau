"""Score the structure-recovery passes (roadmap Tier 3, `parsers/recover.py`).

The passes work on the document IR, so they are scored from saved IR rather than
by converting again: each gold document's pages are rebuilt as one
`document.Document` from the image's path as the harness saved it, the passes
run over it, and the result is written to Markdown the way production writes it
(`serializer.render_units`). Then:

* `structure.py`'s metrics on that Markdown (block F1, types, heading levels and
  tree, tables, order, ...), plus `layout_regions.label_scores` for the types
  only the IR carries (captions, furniture, footnotes);
* **relations**, which Markdown cannot state: a gold relation (`caption_of`,
  `continues_from`, `continues_table`, `footnote_for`) is found when the IR
  draws the same relation between the output blocks matched to its two ends.
  Gold figures have no words, so they are matched to output figures in order on
  each page. `rel_r` is over gold relations, `rel_p` over the IR's relations
  between two different blocks (one joined into a single paragraph is scored as
  that paragraph's block, by the block metrics);
* **words**: every scored page's words must be the same multiset before and
  after the passes, unless reader mode leaves furniture out (`--reader`).

The base IR is the image's path (ADR-047/048): for each OCR'd page, the table
pass's IR (`outputs/tables-tatr/<page>.json`) regrouped by the saved
PP-DocLayoutV3 regions (`outputs/pp-doclayoutv3/<page>.regions.json`) with the
production `layout.apply`; for a text-layer page, its saved IR as it is. On 30
of 31 OCR'd pages that reproduces `outputs/layout-ppdl/` exactly; V4 p27 differs
by the one line ADR-048 records (a 4-thread rounding), and the pass also now
emits figures.

  python benchmarks/bakeoff/recover_eval.py --gold s [--passes all|headings,...] [--docs] [--reader]
  python benchmarks/bakeoff/recover_eval.py --gold t ...

Set S is development, set T is held out. Nothing external is called.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "backend"))

import layout_regions  # noqa: E402
import structure  # noqa: E402
from parsers import document as ir  # noqa: E402
from parsers import layout, recover, serializer  # noqa: E402

OUT = HERE / "outputs"


def page_ir(key: str, number: int) -> ir.Document:
    """One page's IR as the image's path builds it (see the module docstring)."""
    saved = json.loads((OUT / "layout-ppdl" / f"{key}_p{number}.json").read_text())
    if saved["pages"][0]["method"] != "ocr":
        return ir.from_dict(saved)
    base = ir.from_dict(json.loads((OUT / "tables-tatr" / f"{key}_p{number}.json").read_text()))
    raw = json.loads((OUT / "pp-doclayoutv3" / f"{key}_p{number}.regions.json").read_text())["regions"]
    w, h = saved["pages"][0]["size"]
    regions = [layout.Region((r["bbox"][0] * w / 1000, r["bbox"][1] * h / 1000,
                              r["bbox"][2] * w / 1000, r["bbox"][3] * h / 1000), r["category"], r["score"])
               for r in raw]
    base.elements = layout.apply(base.elements, regions)
    return base


def build(key: str, pages: list[int]) -> ir.Document:
    """A gold document's pages as one IR, numbered as in the source PDF."""
    doc = ir.Document()
    for n in pages:
        one = page_ir(key, n)
        info = one.pages[0]
        doc.add_page(ir.PageInfo(number=n, method=info.method, source=info.source,
                                 width=info.width, height=info.height), one.elements)
    return doc


def output_blocks(doc: ir.Document, *, reader: bool = False) -> tuple[list, dict]:
    """The Markdown as structure.py blocks, each knowing its IR elements; and
    element id -> block index."""
    blocks, where = [], {}
    for page in [p.number for p in doc.pages]:
        els = doc.page_elements(page)
        units = serializer.render_units(els, doc.relations, reader=reader)
        rendered = {e.id for _, chain in units for e in chain}
        # Empty figures write nothing, but captions point at them.
        figures = [e for e in els if e.type == ir.FIGURE and e.id not in rendered]
        order = {e.id: k for k, e in enumerate(els)}
        items = [(order[chain[0].id], text, chain) for text, chain in units] + \
                [(order[f.id], None, [f]) for f in figures]
        for _, text, chain in sorted(items, key=lambda x: x[0]):
            if text is None:
                b = structure.Block(type="figure", text="", page=page)
            else:
                md_blocks = structure.parse_output([(page, text)])
                if not md_blocks:
                    continue
                b = md_blocks[0]
                if len(md_blocks) > 1:  # a unit always writes one block; keep its words
                    b.tokens = [t for mb in md_blocks for t in mb.tokens]
            b.ir_type = chain[0].type
            b.ir_ids = [e.id for e in chain]
            for e in chain:
                where[e.id] = len(blocks)
            blocks.append(b)
    return blocks, where


def relation_scores(gold: list, out: list, doc: ir.Document, where: dict) -> dict:
    matched = dict(structure.matched_blocks(gold, out))
    # Figures have no words: match them in order on each page.
    for page in {b.page for b in gold}:
        gf = [i for i, b in enumerate(gold) if b.type == "figure" and b.page == page]
        of = [i for i, b in enumerate(out) if b.type == "figure" and b.page == page]
        matched.update(zip(gf, of))
    by_id = {b.id: i for i, b in enumerate(gold) if b.id}
    drawn = {(r.type, where.get(r.source), where.get(r.target)) for r in doc.relations}
    drawn = {d for d in drawn if d[1] is not None and d[2] is not None and d[1] != d[2]}
    res: dict = {}
    hits = total = 0
    hit_set = set()
    for g, b in enumerate(gold):
        for rel, target in b.relations.items():
            total += 1
            key = (rel, matched.get(g), matched.get(by_id[target]))
            ok = key in drawn
            hits += ok
            if ok:
                hit_set.add(key)
            res[f"{rel}_n"] = res.get(f"{rel}_n", 0) + 1
            res[f"{rel}_hit"] = res.get(f"{rel}_hit", 0) + ok
    for rel in structure.RELATIONS:
        res[f"{rel}_out"] = sum(1 for d in drawn if d[0] == rel)
    res["rel_hit"], res["rel_n"], res["rel_out"] = hits, total, len(drawn)
    res["rel_r"] = hits / total if total else None
    res["rel_p"] = len(hit_set) / len(drawn) if drawn else None
    return res


def words(doc: ir.Document, *, reader: bool = False) -> Counter:
    md = serializer.to_markdown(doc, reader=reader)
    return Counter(t for t, _ in structure.block_tokens(md))


def score(gold_set: str, passes, *, reader: bool = False) -> dict:
    rows = {}
    for name, key, pages, path in layout_regions.gold_documents(gold_set):
        doc = build(key, pages)
        before = words(doc)
        recover.recover(doc, passes)
        after = words(doc, reader=reader)
        gold = structure.parse_gold(path)
        out, where = output_blocks(doc, reader=reader)
        r = structure.score_document(gold, out)
        r.update(layout_regions.label_scores(gold, out))
        r.update(relation_scores(gold, out, doc, where))
        r["words_same"] = before == after
        r["words_lost"] = sum((before - after).values())
        rows[name] = r
    return rows


COLS = ["blk_f", "typ_f", "split", "merge", "ord", "hd_p", "hd_r", "lvl", "htree", "teds"]


def summary(rows: dict) -> str:
    vals = " ".join(f"{layout_regions._mean(rows, c):6.3f}" if layout_regions._mean(rows, c) is not None
                    else "     -" for c in COLS)
    lab = " ".join("/".join(f"{layout_regions._pooled(rows, c, k):.2f}"
                            if layout_regions._pooled(rows, c, k) is not None else "  - "
                            for k in ("r", "p")) for c in ("heading", "caption", "furniture", "footnote"))
    rel = []
    for rel_type in structure.RELATIONS:
        n = sum(r.get(f"{rel_type}_n", 0) for r in rows.values())
        hit = sum(r.get(f"{rel_type}_hit", 0) for r in rows.values())
        o = sum(r.get(f"{rel_type}_out", 0) for r in rows.values())
        rel.append(f"{rel_type.split('_')[0][:4]} {hit}/{n} of {o}")
    same = sum(r["words_same"] for r in rows.values())
    return f"{vals}  {lab}  | {'; '.join(rel)} | words same {same}/{len(rows)}"


def main() -> None:
    args = sys.argv[1:]
    gold_set = args[args.index("--gold") + 1].lower() if "--gold" in args else "s"
    passes = args[args.index("--passes") + 1] if "--passes" in args else "all"
    reader = "--reader" in args
    chosen = recover.PASSES if passes == "all" else (() if passes == "none" else tuple(passes.split(",")))
    print(f"set {gold_set.upper()}  passes={passes}{'  reader' if reader else ''}")
    print(f"{'':10s} docs " + " ".join(f"{c:>6s}" for c in COLS)
          + "   hd R/P    cap R/P    fur R/P    fn R/P   | relations hit/gold of drawn")
    base = score(gold_set, ())
    print(f"{'none':10s} {len(base):4d} {summary(base)}")
    if chosen:
        rows = score(gold_set, chosen, reader=reader)
        print(f"{passes[:10]:10s} {len(rows):4d} {summary(rows)}")
        if "--docs" in args:
            for d in rows:
                b, r = base[d], rows[d]
                diff = {c: f"{b[c] or 0:.2f}->{r[c] or 0:.2f}" for c in COLS + ["rel_r"]
                        if (b.get(c) or 0) != (r.get(c) or 0)}
                print(f"  {d}: {diff} words_lost={r['words_lost']}")
        out = HERE / f"results-recover-{gold_set}.json"
        out.write_text(json.dumps({"none": base, passes: rows}, indent=1, sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    main()
