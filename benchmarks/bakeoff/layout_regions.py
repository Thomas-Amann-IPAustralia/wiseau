"""Layout regions → IR → Markdown, scored on set S (roadmap 14 item 3.7b).

The sidecar test for layout models (`docs/assessment-2026-11-09-ovisocr3-teleocr.md`):
the existing OCR supplies every word, the model only supplies regions and their
categories. Nothing here re-reads a page. Each page starts from the default
path's own document IR (`outputs/<base>/<page>.json`, ADR-043), and a model's
regions (`outputs/<model>/<page>.regions.json`, `layout_models.py`) are applied
to its lines in one of two ways:

  relabel  every element keeps its text and place; an element whose lines lie
           mostly (>= OVERLAP of their area) inside one region takes that
           region's type (paragraph_title -> heading, figure_title -> caption,
           header/footer/number -> page furniture, ...).
  regroup  every line goes to the region holding most of its area (>= OVERLAP);
           each region becomes one element of its type, in the model's reading
           order. A line no region holds stays in what is left of its element,
           placed after the region that took the element's previous line.

Only OCR'd pages change: a born-digital page keeps PyMuPDF4LLM's structure
(the item is about scans). Table Transformer tables (base `tables-tatr`) are
never split or relabelled; they keep their cells and move as one unit.

The settings (OVERLAP, CATEGORY_TYPES) were fixed from the categories' meaning
before any page was scored, and are not tuned on set S (which holds pages from
held-out sets D, E and F).

  python layout_regions.py build MODEL [--base pymupdf|tables-tatr]
      -> outputs/<MODEL>-<variant>[-tatr]/<page>.md + .json for every page MODEL has regions for
  python layout_regions.py score ENGINE [ENGINE ...]
      -> set S structure metrics (structure.py's, unchanged) plus `lab`, the
         element types Markdown cannot write (caption, page furniture, footnote)
         read from the IR, and a word check: the output's words must equal its
         base's words, page by page, as a multiset.

No model runs here and nothing external is called.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import structure  # noqa: E402
from parsers import document as ir, serializer  # noqa: E402  (structure.py put backend/ on the path)

OUT = HERE / "outputs"
OVERLAP = 0.5
# A layout category -> (IR element type, heading level or None). Unlisted
# categories are body text.
CATEGORY_TYPES = {
    "doc_title": ("title", 1),
    "paragraph_title": ("heading", 2),
    "figure_title": ("caption", None),
    "header": ("page_header", None), "header_image": ("page_header", None),
    "footer": ("page_footer", None), "footer_image": ("page_footer", None),
    "number": ("page_number", None),
    "footnote": ("footnote", None), "vision_footnote": ("footnote", None),
    "display_formula": ("equation", None), "formula": ("equation", None),
}
BODY = ("text", None)
# Types compared by `lab`, as classes (title and heading are one class).
LABEL_CLASS = {"title": "heading", "heading": "heading", "caption": "caption",
               "page_header": "furniture", "page_footer": "furniture", "page_number": "furniture",
               "footnote": "footnote"}


# --- geometry -------------------------------------------------------------------


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def region_boxes(regions: list[dict], page_size) -> list[list[float]]:
    """Regions' 0-1000 boxes in PDF points (the IR's unit)."""
    w, h = page_size
    return [[r["bbox"][0] * w / 1000, r["bbox"][1] * h / 1000, r["bbox"][2] * w / 1000, r["bbox"][3] * h / 1000]
            for r in regions]


def best_region(box, boxes) -> int | None:
    """The region holding the largest share (>= OVERLAP) of ``box``'s area; ties go
    to the smaller region (the more specific one)."""
    area = _area(box)
    if area <= 0:
        return None
    best, best_key = None, None
    for i, rb in enumerate(boxes):
        share = _inter(box, rb) / area
        if share >= OVERLAP:
            key = (share, -_area(rb))
            if best_key is None or key > best_key:
                best, best_key = i, key
    return best


# --- building -------------------------------------------------------------------


def _typed(el: dict, category: str, model: str, score) -> dict:
    kind, level = CATEGORY_TYPES.get(category, BODY)
    out = {**el, "type": kind, "signal": f"layout:{model}", "confidence": None, "layout_category": category}
    out.pop("level", None)
    if level is not None:
        out["level"] = level
    if score is not None:
        out["score"] = score
    return out


def _is_kept(el: dict) -> bool:
    """An element the layout pass never touches: a Table Transformer table."""
    return el["type"] == "table" and el.get("signal") == "tatr"


def relabel(elements: list[dict], regions: list[dict], boxes, model: str) -> list[dict]:
    out = []
    for el in elements:
        if _is_kept(el) or not el.get("lines"):
            out.append(el)
            continue
        held = Counter()
        total = 0.0
        for ln in el["lines"]:
            a = _area(ln["bbox"])
            total += a
            i = best_region(ln["bbox"], boxes)
            if i is not None:
                held[i] += a
        if total and held:
            i, a = held.most_common(1)[0]
            if a / total >= OVERLAP:
                out.append(_typed(el, regions[i]["category"], model, regions[i].get("score")))
                continue
        out.append(el)
    return out


def regroup(elements: list[dict], regions: list[dict], boxes, model: str) -> list[dict]:
    """Lines regrouped into regions, in the model's order (see module docstring)."""
    # Units: ("region", i) or ("rest", element index) or ("kept", element index).
    region_lines: dict[int, list[dict]] = {}
    rests: dict[int, list[dict]] = {}
    keys: dict[tuple, float] = {}
    last_region = -1.0  # the model rank of the region that took the latest line
    sub = 0
    for e, el in enumerate(elements):
        if _is_kept(el) or not el.get("lines"):
            i = best_region(el["bbox"], boxes) if el.get("bbox") else None
            sub += 1
            keys[("kept", e)] = (i if i is not None else last_region) + sub * 1e-6 + (0 if i is not None else 0.5)
            continue
        for ln in el["lines"]:
            i = best_region(ln["bbox"], boxes)
            if i is None:
                if e not in rests:
                    sub += 1
                    keys[("rest", e)] = last_region + 0.5 + sub * 1e-6
                rests.setdefault(e, []).append(ln)
            else:
                region_lines.setdefault(i, []).append(ln)
                keys.setdefault(("region", i), float(i))
                last_region = float(i)
    out = []
    for unit in sorted(keys, key=keys.get):
        kind, n = unit
        if kind == "kept":
            out.append(elements[n])
        elif kind == "rest":
            el = elements[n]
            lines = rests[n]
            out.append({**el, "text": "\n".join(ln["text"] for ln in lines), "lines": lines,
                        "bbox": _around(lines), "text_format": "plain"})
        else:
            lines = region_lines[n]
            base = {"id": f"r{n + 1}", "page": elements[0].get("page"), "source": "layout-regroup",
                    "text_format": "plain", "text": "\n".join(ln["text"] for ln in lines), "lines": lines,
                    "bbox": _around(lines)}
            out.append(_typed(base, regions[n]["category"], model, regions[n].get("score")))
    return out


def _around(lines):
    return [min(ln["bbox"][0] for ln in lines), min(ln["bbox"][1] for ln in lines),
            max(ln["bbox"][2] for ln in lines), max(ln["bbox"][3] for ln in lines)]


def render(el: dict) -> str:
    """One element as Markdown for scoring: the serializer's own rendering of the
    element, with a heading marker for a heading or title the layout pass typed
    (the serializer has no rule for typed plain text yet; that is 3.1's job)."""
    if el.get("signal", "").startswith("layout:") and el["type"] in ("heading", "title"):
        return "#" * (el.get("level") or 2) + " " + " ".join(el["text"].split("\n"))
    return serializer.render_element(_element(el))


def _element(d: dict) -> "ir.Element":
    """Just enough of a `document.Element` from its JSON for the serializer."""
    rows = tuple(tuple(ir.Cell(c["text"], header=c.get("header", False), row_span=c.get("row_span", 1),
                               col_span=c.get("col_span", 1)) for c in row) for row in d.get("rows") or ())
    return ir.Element(type=d["type"], text=d["text"], source=d["source"], text_format=d["text_format"],
                      rows=rows, signal=d.get("signal"))


def build(model: str, base: str) -> None:
    for variant, fn in (("relabel", relabel), ("regroup", regroup)):
        name = f"{model}-{variant}" + ("-tatr" if base == "tables-tatr" else "")
        out_dir = OUT / name
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for rpath in sorted((OUT / model).glob("*.regions.json")):
            page = rpath.name[: -len(".regions.json")]
            ir_path = OUT / base / f"{page}.json"
            if not ir_path.exists():
                continue
            ir = json.loads(ir_path.read_text())
            rec = json.loads(rpath.read_text())
            elements = ir["elements"]
            if ir["pages"][0]["method"] == "ocr" and rec.get("regions"):
                boxes = region_boxes(rec["regions"], ir["pages"][0]["size"])
                elements = fn(elements, rec["regions"], boxes, model)
            # A failed or empty model page keeps the base's IR: the pass degrades.
            md = "\n\n".join(t for t in (render(el) for el in elements) if t.strip())
            base_md = (OUT / base / f"{page}.md").read_text(encoding="utf-8")
            if Counter(t for t, _ in structure.block_tokens(md)) != \
                    Counter(t for t, _ in structure.block_tokens(base_md)):
                raise SystemExit(f"{name} {page}: words differ from {base}")
            (out_dir / f"{page}.md").write_text(md + "\n", encoding="utf-8")
            (out_dir / f"{page}.json").write_text(json.dumps({**ir, "elements": elements}, indent=1,
                                                             ensure_ascii=False) + "\n", encoding="utf-8")
            n += 1
        print(f"{name}: {n} pages")


# --- scoring --------------------------------------------------------------------


def _ir_blocks(pages: list[tuple[int, dict, str]]) -> list[structure.Block]:
    """Output blocks from the Markdown (as structure.py reads them), each carrying
    its IR element's type: block i of a page's Markdown is element i (empty
    elements are skipped on both sides)."""
    blocks = []
    for page, ir, md in pages:
        md_blocks = structure.parse_output([(page, md)])
        els = [el for el in ir["elements"] if render(el).strip()]
        if len(els) == len(md_blocks):
            for b, el in zip(md_blocks, els):
                b.ir_type = el["type"]
        blocks.extend(md_blocks)
    return blocks


def label_scores(gold, out) -> dict:
    """Recall and precision of the non-Markdown types over matched blocks."""
    matched = structure.matched_blocks(gold, out)
    res = {}
    for cls in ("heading", "caption", "furniture", "footnote"):
        g = [(gi, oi) for gi, oi in matched if LABEL_CLASS.get(gold[gi].type) == cls]
        o = [(gi, oi) for gi, oi in matched if LABEL_CLASS.get(getattr(out[oi], "ir_type", "")) == cls]
        hit = [(gi, oi) for gi, oi in g if LABEL_CLASS.get(getattr(out[oi], "ir_type", "")) == cls]
        res[f"{cls}_r"] = len(hit) / len(g) if g else None
        res[f"{cls}_p"] = len(hit) / len(o) if o else None
        res[f"{cls}_n"] = len(g)
    return res


def score(engines: list[str]) -> dict:
    results = {}
    for engine in engines:
        rows = {}
        for name, key, pages, path in structure.gold_documents():
            mds = [OUT / engine / f"{key}_p{n}.md" for n in pages]
            irs = [OUT / engine / f"{key}_p{n}.json" for n in pages]
            if not all(f.exists() for f in mds + irs):
                continue
            gold = structure.parse_gold(path)
            pages_in = [(n, json.loads(j.read_text()), m.read_text(encoding="utf-8"))
                        for n, j, m in zip(pages, irs, mds)]
            out = _ir_blocks(pages_in)
            r = structure.score_document(gold, out)
            r.update(label_scores(gold, out))
            rows[name] = r
        results[engine] = rows
    return results


def _mean(rows: dict, k: str):
    vals = [r[k] for r in rows.values() if r.get(k) is not None]
    return sum(vals) / len(vals) if vals else None


def _pooled(rows: dict, cls: str):
    """Recall pooled over documents (blocks, not documents, weigh)."""
    n = sum(r[f"{cls}_n"] for r in rows.values())
    hit = sum(round((r[f"{cls}_r"] or 0) * r[f"{cls}_n"]) for r in rows.values())
    return hit / n if n else None


def main() -> None:
    if sys.argv[1] == "build":
        base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "pymupdf"
        build(sys.argv[2], base)
        return
    engines = [a for a in sys.argv[2:] if not a.startswith("--")]
    results = score(engines)
    (HERE / "results-layout.json").write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    cols = ["blk_f", "typ_f", "split", "merge", "ord", "hd_p", "hd_r", "htree", "teds"]
    print(f"{'engine':34s} docs " + " ".join(f"{c:>6s}" for c in cols)
          + "  hd_R  cap_R  fur_R  fn_R  (pooled recall, IR types)")
    for engine, rows in results.items():
        vals = " ".join(f"{_mean(rows, c):6.3f}" if _mean(rows, c) is not None else "     -" for c in cols)
        lab = " ".join(f"{_pooled(rows, c):5.3f}" if _pooled(rows, c) is not None else "    -"
                       for c in ("heading", "caption", "furniture", "footnote"))
        print(f"{engine:34s} {len(rows):4d} {vals}  {lab}")


if __name__ == "__main__":
    main()
