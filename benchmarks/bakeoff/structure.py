"""Score document *structure* against the structural gold set (roadmap 14 item 2.3).

`score.py` measures the words; this measures what the words are arranged into:
blocks, their types, headings and their hierarchy, tables, emphasis, and the
relations between blocks (captions, continuations). The gold set is set S,
`structure/S*.md`: each file is one document (a run of one to three pages) written
as the *ideal Markdown* of those pages, annotated where Markdown has no syntax.
The format is in `structure/README.md`.

Both sides are cut into blocks the same way (`serializer.split_markdown`, at
blank lines outside code fences) and into tokens the same way as `score.py`
(runs of letters and digits, NFKC, lower case), so a block's identity is its
words, not its spelling. Gold and output tokens are aligned in order, with extra
passes for blocks the output puts in a different order (as in `text_layer.py`).

Metrics per document (and means over documents):
  blk_p / blk_r / blk_f  block precision / recall / F1. A gold block and an output
         block match when each is the other's main partner and the pair shares at
         least half of each block's tokens.
  typ_f  the same, counting a match only when the types agree, in the Markdown
         view: gold types Markdown cannot write (caption, page number, footnote,
         ...) count as paragraphs, since that is all an output in Markdown can be.
  split  share of gold blocks whose tokens an output spreads over 2+ blocks.
  merge  share of gold blocks that share an output block with another gold block.
  ord    block reading-order error: 1 - (longest run of matched blocks in gold
         order) / matched blocks, over the output's order. 0 = gold order.
  hd_p / hd_r  heading precision / recall (a matched block that is a heading on
         both sides).
  lvl    heading level accuracy on matched headings, after replacing each side's
         levels by their rank in the document (so # / ## and ## / ### agree).
  htree  heading tree similarity, 1 - tree edit distance / larger tree size, over
         the trees the headings' levels make (Zhang-Shasha; renaming costs the
         token distance between the two headings' texts).
  teds   tree-edit-distance similarity of each gold table with the output that
         covers it (Zhong et al., 2020): table/row/cell trees; a cell renamed
         costs the normalised edit distance of the two cells' texts; clamped at 0.
         Output text that is not a table counts as one row per line with one
         cell; an HTML cell spanning n columns counts as its text and n - 1 empty
         cells. A table continued by another (`continues_table`: side-by-side
         panels, or the next page) is scored as one logical table, its repeated
         header row dropped. Mean over logical tables; teds_s ignores cell text.
  em_r / em_p  emphasis recall / precision: of the aligned tokens outside gold
         headings, the share of gold-emphasised ones (bold or italic) that the
         output also emphasises, and the share of output-emphasised ones the gold
         emphasises. "-" when the output emphasises nothing there (em_p) or the
         gold does not (em_r).
  rel_r  share of gold relations (caption_of, continues_from, continues_table,
         footnote_for) the output states. Markdown states none, so this is 0 for
         every engine scored from Markdown; it is here so IR-level relations can
         be scored when an engine produces them.

  python benchmarks/bakeoff/structure.py              # every engine with outputs for set S,
                                                      # and `pymupdf-ir`: the default path's IR
                                                      # (outputs/pymupdf/<page>.json) before
                                                      # it is serialized
  python benchmarks/bakeoff/structure.py --docs       # also per document
  python benchmarks/bakeoff/structure.py --check      # parse the gold files and report
  python benchmarks/bakeoff/structure.py --self-check # each gold file scored against itself

Writes results-structure.json. No engine runs and nothing external is called.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from parsers import serializer  # noqa: E402

GOLD_DIR = HERE / "structure"
ENGINES = ["pymupdf", "tables-tatr", "docling", "glm-ocr", "gemini-3.5-flash"]
RELATIONS = ("caption_of", "continues_from", "continues_table", "footnote_for")
# Gold types an output in Markdown can express; the rest are read as paragraphs.
MARKDOWN_TYPES = {"heading", "table", "list", "paragraph", "code", "figure", "rule"}
GOLD_TYPES = MARKDOWN_TYPES | {"title", "caption", "footnote", "page_number", "page_header",
                               "page_footer", "equation", "list_item", "text"}

_PAGE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")
_NOTE = re.compile(r"^<!--(.*?)-->[ \t]*(?:\n|$)", re.S)
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>\n]*>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_EM_TAG = re.compile(r"<(b|strong|i|em)\b[^>]*>(.*?)</\1>", re.S | re.I)
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_ITALIC = re.compile(r"(?<![*\w])([*_])(?=[^\s*_|])([^*_|\n]+?)(?<=[^\s*_|])\1(?![*\w])")
_WORD = re.compile(r"[^\W_]+")


# --- blocks and tokens -------------------------------------------------------


@dataclass
class Block:
    """One block of a document, gold or output."""

    type: str
    text: str
    page: int | None
    level: int | None = None
    id: str = ""
    relations: dict = field(default_factory=dict)
    tokens: list = field(default_factory=list)   # (token, emphasised)
    rows: list | None = None                     # cell texts, when read from an IR table


def block_tokens(markdown: str) -> list[tuple[str, bool]]:
    """Printed tokens of a Markdown block, each flagged if it is bold or italic."""
    text = _HTML_COMMENT.sub(lambda m: " " * len(m.group()), markdown)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", lambda m: " " * len(m.group()), text)
    text = re.sub(r"\]\([^)]*\)", lambda m: " " * len(m.group()), text)
    text = re.sub(r"\\[a-zA-Z]+", lambda m: " " * len(m.group()), text)
    em = [False] * len(text)
    for m in _EM_TAG.finditer(text):
        for i in range(*m.span(2)):
            em[i] = True
    text = _HTML_TAG.sub(lambda m: " " * len(m.group()), text)
    for pattern in (_BOLD, _ITALIC):
        for m in pattern.finditer(text):
            for i in range(*m.span(2)):
                em[i] = True
    out = []
    for m in _WORD.finditer(text):
        flag = em[m.start()]
        for t in _WORD.findall(unicodedata.normalize("NFKC", m.group()).lower()):
            out.append((t, flag))
    return out


def _typed(markdown: str) -> tuple[str, int | None]:
    els = serializer.split_markdown(markdown, "x")
    if not els:
        return "paragraph", None
    # An HTML table is a table, whatever its first characters.
    if re.match(r"\s*<table", markdown, re.I):
        return "table", None
    return els[0].type, els[0].level


def parse_gold(path: Path) -> list[Block]:
    """A gold file as blocks: `<!-- page N -->` sets the page; a leading
    `<!-- TYPE key=value ... -->` line sets a block's type, id and relations."""
    blocks: list[Block] = []
    page = None
    for el in serializer.split_markdown(path.read_text(encoding="utf-8"), "gold"):
        text = el.text.strip("\n")
        m = _PAGE.match(text.strip())
        if m:
            page = int(m.group(1))
            continue
        kind, attrs = None, {}
        note = _NOTE.match(text)
        if note:
            for word in note.group(1).split():
                if "=" in word:
                    k, v = word.split("=", 1)
                    attrs[k] = v
                elif word in GOLD_TYPES:
                    kind = word
                else:
                    raise ValueError(f"{path.name}: unknown annotation {word!r} in {note.group(0)!r}")
            text = text[note.end():]
        md_type, level = _typed(text) if text.strip() else ("figure", None)
        b = Block(type=kind or md_type, text=text, page=page, level=level if (kind or md_type) == "heading" else None,
                  id=attrs.pop("id", ""), relations={k: v for k, v in attrs.items()})
        unknown = set(b.relations) - set(RELATIONS)
        if unknown:
            raise ValueError(f"{path.name}: unknown relation {sorted(unknown)}")
        b.tokens = block_tokens(text)
        blocks.append(b)
    ids = {b.id for b in blocks if b.id}
    for b in blocks:
        for rel, target in b.relations.items():
            if target not in ids:
                raise ValueError(f"{path.name}: {rel}={target} names no block")
    return blocks


def parse_output(pages: list[tuple[int, str]]) -> list[Block]:
    """An engine's Markdown for each page, as blocks."""
    blocks = []
    for page, markdown in pages:
        for el in serializer.split_markdown(markdown, "out"):
            text = el.text
            if not _HTML_COMMENT.sub("", text).strip():
                if "image" in text.lower() or "figure" in text.lower():
                    blocks.append(Block(type="figure", text="", page=page))
                continue
            text = re.sub(r"^(?:<!--.*?-->[ \t]*\n)+", "", text)  # a leading annotation line
            kind, level = _typed(text)
            b = Block(type=kind, text=text, page=page, level=level)
            b.tokens = block_tokens(text)
            blocks.append(b)
    return blocks


def parse_ir(pages: list[tuple[int, dict]]) -> list[Block]:
    """The default path's document IR (`outputs/pymupdf/<page>.json`, ADR-043) as
    blocks: element types as the IR states them, tables as their stitched rows.
    What the IR knows before the serializer flattens it to Markdown."""
    blocks = []
    for page, tree in pages:
        for el in tree["elements"]:
            kind = el["type"]
            if el["text_format"] == "markdown":
                text = el["text"]
            else:
                kind = "paragraph" if kind == "text" else kind
                text = el["text"]
            b = Block(type=kind, text=text, page=page, level=el.get("level"))
            if el.get("rows"):
                b.rows = [[c["text"] for c in row] for row in el["rows"]]
            b.tokens = block_tokens(text) if el["text_format"] == "markdown" else \
                [(t, False) for t, _ in block_tokens(text)]
            blocks.append(b)
    return blocks


# --- alignment ---------------------------------------------------------------


def align(a: list[str], b: list[str], rounds: int = 32) -> dict[int, int]:
    """Token alignment a -> b: order-preserving passes, then passes over what is
    left (runs of 2+ only), so blocks in a different order still align."""
    pairs: dict[int, int] = {}
    free_a, free_b = list(range(len(a))), list(range(len(b)))
    for r in range(rounds):
        sm = SequenceMatcher(None, [a[i] for i in free_a], [b[j] for j in free_b], autojunk=False)
        got = False
        for x, y, size in sm.get_matching_blocks():
            if size == 0 or (r and size < 2):
                continue
            for k in range(size):
                pairs[free_a[x + k]] = free_b[y + k]
            got = True
        if not got:
            break
        used_b = set(pairs.values())
        free_a = [i for i in free_a if i not in pairs]
        free_b = [j for j in free_b if j not in used_b]
    return pairs


# --- tree edit distance (Zhang & Shasha 1989) --------------------------------


class Node:
    __slots__ = ("label", "children")

    def __init__(self, label, children=None):
        self.label = label
        self.children = children or []


def _postorder(root: Node):
    nodes, lmd = [], []

    def walk(n: Node) -> int:
        first = None
        for c in n.children:
            f = walk(c)
            first = f if first is None else first
        nodes.append(n)
        lmd.append(len(nodes) - 1 if first is None else first)
        return lmd[-1]

    walk(root)
    return nodes, lmd


def tree_distance(t1: Node, t2: Node, rename) -> float:
    n1, l1 = _postorder(t1)
    n2, l2 = _postorder(t2)
    kr1 = sorted({max(i for i in range(len(n1)) if l1[i] == l1[k]) for k in range(len(n1))})
    kr2 = sorted({max(j for j in range(len(n2)) if l2[j] == l2[k]) for k in range(len(n2))})
    td = [[0.0] * len(n2) for _ in n1]
    for i in kr1:
        for j in kr2:
            li, lj = l1[i], l2[j]
            m, n = i - li + 2, j - lj + 2
            fd = [[0.0] * n for _ in range(m)]
            for x in range(1, m):
                fd[x][0] = fd[x - 1][0] + 1
            for y in range(1, n):
                fd[0][y] = fd[0][y - 1] + 1
            for x in range(1, m):
                for y in range(1, n):
                    a, b = li + x - 1, lj + y - 1
                    if l1[a] == li and l2[b] == lj:
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[x - 1][y - 1] + rename(n1[a].label, n2[b].label))
                        td[a][b] = fd[x][y]
                    else:
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[l1[a] - li][l2[b] - lj] + td[a][b])
    return td[-1][-1]


def _size(t: Node) -> int:
    return 1 + sum(_size(c) for c in t.children)


@lru_cache(maxsize=200_000)
def _str_dist(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


def _cell_rename(a, b) -> float:
    if a[0] != b[0]:
        return 1.0
    if a[0] != "td":
        return 0.0
    return _str_dist(a[1], b[1])


def _norm_cell(text: str) -> str:
    return " ".join(t for t, _ in block_tokens(text))


class _HtmlTable(HTMLParser):
    """Rows of cell texts. A cell spanning n columns becomes its text and n - 1
    empty cells, the way the gold writes a row label printed across the table."""

    def __init__(self):
        super().__init__()
        self.rows, self._cell, self._span = [], None, 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append([])
            self._cell = []
            span = dict(attrs).get("colspan") or "1"
            self._span = int(span) if span.isdigit() else 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(" ".join(self._cell))
            self.rows[-1].extend([""] * (self._span - 1))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def table_rows(block: Block) -> list[list[str]]:
    """A block's rows of cell texts: a pipe or HTML table's cells, else one cell a line."""
    if block.rows is not None:
        return block.rows
    text = block.text
    if re.search(r"<table", text, re.I):
        parser = _HtmlTable()
        parser.feed(text)
        return [r for r in parser.rows if r]
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if lines and all(ln.lstrip().startswith("|") for ln in lines):
        rows = []
        for ln in lines:
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip().strip("|"))]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
                continue
            rows.append(cells)
        return rows
    return [[ln] for ln in lines]


def table_tree(rows: list[list[str]], content: bool = True) -> Node:
    return Node(("table",), [Node(("tr",), [Node(("td", _norm_cell(c) if content else ""))
                                            for c in r]) for r in rows])


def teds(gold_rows, out_rows, content: bool = True) -> float:
    g, o = table_tree(gold_rows, content), table_tree(out_rows, content)
    # Clamped at 0, as TEDS implementations do: an edit script between two trees
    # of different shapes can cost more than the larger tree has nodes.
    return max(0.0, 1.0 - tree_distance(g, o, _cell_rename) / max(_size(g), _size(o)))


# --- headings ------------------------------------------------------------------


def _ranks(levels: list[int]) -> dict[int, int]:
    return {lv: i + 1 for i, lv in enumerate(sorted(set(levels)))}


def heading_tree(headings: list[Block]) -> Node:
    ranks = _ranks([h.level or 1 for h in headings])
    root = Node(("root", ""))
    stack = [(0, root)]
    for h in headings:
        lv = ranks[h.level or 1]
        while stack[-1][0] >= lv:
            stack.pop()
        node = Node(("h", " ".join(t for t, _ in h.tokens)))
        stack[-1][1].children.append(node)
        stack.append((lv, node))
    return root


def _heading_rename(a, b) -> float:
    if a[0] != b[0]:
        return 1.0
    sa, sb = set(a[1].split()), set(b[1].split())
    if not sa and not sb:
        return 0.0
    return 1.0 - len(sa & sb) / len(sa | sb)


# --- scoring one document --------------------------------------------------------


def _table_chains(gold: list[Block]) -> list[list[int]]:
    """Gold tables grouped into logical tables by `continues_table`, in order."""
    by_id = {b.id: n for n, b in enumerate(gold) if b.id}
    chains: list[list[int]] = []
    where: dict[int, list[int]] = {}
    for n, b in enumerate(gold):
        if b.type != "table":
            continue
        head = by_id.get(b.relations.get("continues_table", ""))
        if head is not None and head in where:
            where[head].append(n)
            where[n] = where[head]
        else:
            chain = [n]
            chains.append(chain)
            where[n] = chain
    return chains


def _md_type(kind: str) -> str:
    if kind == "title":
        return "heading"
    return kind if kind in MARKDOWN_TYPES else "paragraph"


def score_document(gold: list[Block], out: list[Block]) -> dict:
    g_tok, g_of = [], []
    for n, b in enumerate(gold):
        for t, _ in b.tokens:
            g_tok.append(t)
            g_of.append(n)
    o_tok, o_of = [], []
    for n, b in enumerate(out):
        for t, _ in b.tokens:
            o_tok.append(t)
            o_of.append(n)
    pairs = align(g_tok, o_tok)

    # Shared tokens between each gold block and each output block.
    share: dict[tuple[int, int], int] = {}
    for gi, oi in pairs.items():
        key = (g_of[gi], o_of[oi])
        share[key] = share.get(key, 0) + 1
    g_len = [len(b.tokens) for b in gold]
    o_len = [len(b.tokens) for b in out]
    best_o: dict[int, int] = {}
    best_g: dict[int, int] = {}
    for (g, o), n in sorted(share.items()):
        if n > share.get((g, best_o.get(g, -1)), 0):
            best_o[g] = o
        if n > share.get((best_g.get(o, -1), o), 0):
            best_g[o] = g
    matched = [(g, o) for g, o in best_o.items()
               if best_g.get(o) == g and share[(g, o)] >= 0.5 * g_len[g] and share[(g, o)] >= 0.5 * o_len[o]]
    n_gold = sum(1 for n in g_len if n)
    n_out = sum(1 for n in o_len if n)
    p = len(matched) / n_out if n_out else 0.0
    r = len(matched) / n_gold if n_gold else 0.0
    typed = [(g, o) for g, o in matched if _md_type(gold[g].type) == out[o].type]
    tp, tr = (len(typed) / n_out if n_out else 0.0), (len(typed) / n_gold if n_gold else 0.0)

    # Splits and merges.
    split = merged = 0
    holders: dict[int, set] = {}
    for (g, o), n in share.items():
        if n >= max(2, 0.1 * g_len[g]):
            holders.setdefault(g, set()).add(o)
    for g, os in holders.items():
        if len(os) > 1:
            split += 1
    for g, os in holders.items():
        if any(any(o in holders[h] for o in os) for h in holders if h != g):
            merged += 1

    # Block order over matched pairs, in the output's order.
    seq = [g for g, o in sorted(matched, key=lambda x: x[1])]
    lis: list[int] = []
    for v in seq:  # patience sorting
        lo, hi = 0, len(lis)
        while lo < hi:
            mid = (lo + hi) // 2
            if lis[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(lis):
            lis.append(v)
        else:
            lis[lo] = v
    order = 1 - len(lis) / len(seq) if seq else None

    # Headings.
    g_heads = [g for g, b in enumerate(gold) if _md_type(b.type) == "heading"]
    o_heads = [o for o, b in enumerate(out) if b.type == "heading"]
    hd_pairs = [(g, o) for g, o in matched if g in g_heads and o in o_heads]
    hd_p = len(hd_pairs) / len(o_heads) if o_heads else None
    hd_r = len(hd_pairs) / len(g_heads) if g_heads else None
    lvl = None
    if hd_pairs:
        gr = _ranks([gold[g].level or 1 for g in g_heads])
        orr = _ranks([out[o].level or 1 for o in o_heads])
        lvl = sum(gr[gold[g].level or 1] == orr[out[o].level or 1] for g, o in hd_pairs) / len(hd_pairs)
    htree = None
    if g_heads:
        gt, ot = heading_tree([gold[g] for g in g_heads]), heading_tree([out[o] for o in o_heads])
        htree = 1 - tree_distance(gt, ot, _heading_rename) / max(_size(gt) - 1, _size(ot) - 1, 1)

    # Tables: each logical gold table (a table and the panels or pages that
    # continue it, `continues_table`) against the output blocks holding its tokens.
    teds_c, teds_s = [], []
    for chain in _table_chains(gold):
        gold_rows: list[list[str]] = []
        for g in chain:
            rows = table_rows(gold[g])
            if gold_rows and rows and rows[0] == gold_rows[0]:
                rows = rows[1:]  # a continuation's repeated header
            gold_rows.extend(rows)
        os = sorted({o for (gg, o), n in share.items() if gg in chain and n >= max(2, 0.1 * g_len[gg])})
        out_rows: list[list[str]] = []
        for o in os:
            rows = table_rows(out[o])
            if out_rows and rows and rows[0] == out_rows[0]:
                rows = rows[1:]  # the same rule for an output that repeats the header
            out_rows.extend(rows)
        teds_c.append(teds(gold_rows, out_rows, True) if out_rows else 0.0)
        teds_s.append(teds(gold_rows, out_rows, False) if out_rows else 0.0)

    # Emphasis over aligned tokens, leaving out gold headings: a heading's type
    # already says it stands out, so writing it bold is neither right nor wrong.
    g_em = [em for b in gold for _, em in b.tokens]
    o_em = [em for b in out for _, em in b.tokens]
    in_heading = [_md_type(b.type) == "heading" for b in gold for _ in b.tokens]
    scored = [(gi, oi) for gi, oi in pairs.items() if not in_heading[gi]]
    both = sum(1 for gi, oi in scored if g_em[gi] and o_em[oi])
    g_em_n = sum(1 for gi, _ in scored if g_em[gi])
    o_em_n = sum(1 for _, oi in scored if o_em[oi])

    n_rel = sum(len(b.relations) for b in gold)
    return {
        "blk_p": p, "blk_r": r, "blk_f": _f(p, r), "typ_f": _f(tp, tr),
        "split": split / n_gold if n_gold else None, "merge": merged / n_gold if n_gold else None,
        "ord": order, "hd_p": hd_p, "hd_r": hd_r, "lvl": lvl, "htree": htree,
        "teds": _mean(teds_c), "teds_s": _mean(teds_s),
        "em_r": both / g_em_n if g_em_n else None, "em_p": both / o_em_n if o_em_n else None,
        "rel_r": 0.0 if n_rel else None,
        "n_gold": n_gold, "n_out": n_out, "n_tables": len(teds_c), "n_rel": n_rel,
    }


def _f(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


# --- driver --------------------------------------------------------------------

_NAME = re.compile(r"^(S\d+)_([A-Za-z0-9]+)_p(\d+)(?:-(\d+))?$")
METRICS = ["blk_p", "blk_r", "blk_f", "typ_f", "split", "merge", "ord", "hd_p", "hd_r", "lvl", "htree",
           "teds", "teds_s", "em_r", "em_p", "rel_r"]


def gold_documents() -> list[tuple[str, str, list[int], Path]]:
    docs = []
    for path in sorted(GOLD_DIR.glob("S*.md")):
        m = _NAME.match(path.stem)
        if not m:
            continue
        first, last = int(m.group(3)), int(m.group(4) or m.group(3))
        docs.append((m.group(1), m.group(2), list(range(first, last + 1)), path))
    return docs


def main() -> None:
    docs = gold_documents()
    if "--self-check" in sys.argv:
        # Each gold file scored as if it were an engine's output must be perfect.
        # Known exception: S05's page number "- 7" reads as a list item in the
        # Markdown view (typ_f 0.833); the gold is frozen, so it stays.
        for name, key, pages, path in docs:
            parts = re.split(r"<!-- page (\d+) -->", path.read_text(encoding="utf-8"))
            out = parse_output([(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)])
            r = score_document(parse_gold(path), out)
            off = {k: round(v, 3) for k, v in r.items() if k in METRICS and v is not None and k != "rel_r"
                   and (v != 0 if k in ("split", "merge", "ord") else abs(v - 1) > 1e-9)}
            print(name, "perfect" if not off else off)
        return
    if "--check" in sys.argv:
        for name, key, pages, path in docs:
            blocks = parse_gold(path)
            kinds: dict[str, int] = {}
            for b in blocks:
                kinds[b.type] = kinds.get(b.type, 0) + 1
            rels = sum(len(b.relations) for b in blocks)
            print(f"{name} {key} p{pages}: {len(blocks)} blocks {kinds}, {rels} relations")
        return
    results: dict = {}
    for engine in ENGINES:
        rows = {}
        for name, key, pages, path in docs:
            files = [HERE / "outputs" / engine / f"{key}_p{n}.md" for n in pages]
            if not all(f.exists() for f in files):
                continue
            out = parse_output([(n, f.read_text(encoding="utf-8")) for n, f in zip(pages, files)])
            rows[name] = score_document(parse_gold(path), out)
        if rows:
            results[engine] = rows
    # The default path's IR, before serialization (its tables keep their rows).
    rows = {}
    for name, key, pages, path in docs:
        files = [HERE / "outputs" / "pymupdf" / f"{key}_p{n}.json" for n in pages]
        if all(f.exists() for f in files):
            out = parse_ir([(n, json.loads(f.read_text())) for n, f in zip(pages, files)])
            rows[name] = score_document(parse_gold(path), out)
    if rows:
        results["pymupdf-ir"] = rows
    (HERE / "results-structure.json").write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    _print(results, docs, per_doc="--docs" in sys.argv)


def _fmt(v) -> str:
    return "  -  " if v is None else f"{v:.3f}"


def _print(results: dict, docs, per_doc: bool) -> None:
    names = [d[0] for d in docs]
    print("engine            docs " + " ".join(f"{m:>6}" for m in METRICS))
    for engine, rows in results.items():
        mean = {m: _mean([rows[d][m] for d in rows]) for m in METRICS}
        print(f"{engine:<17} {len(rows):>4} " + " ".join(f"{_fmt(mean[m]):>6}" for m in METRICS))
    # Engines with outputs for only a page or two (a paid model run on set A)
    # would shrink the common ground to nothing; compare the rest.
    compared = {e: rows for e, rows in results.items() if len(rows) >= 3}
    common = [d for d in names if all(d in rows for rows in compared.values())]
    if common and len(compared) > 1:
        print(f"\nOn the {len(common)} documents {', '.join(compared)} all have ({', '.join(common)}):")
        for engine, rows in compared.items():
            mean = {m: _mean([rows[d][m] for d in common]) for m in METRICS}
            print(f"{engine:<17} {len(common):>4} " + " ".join(f"{_fmt(mean[m]):>6}" for m in METRICS))
    if per_doc:
        for engine, rows in results.items():
            print(f"\n{engine}")
            for d, row in rows.items():
                print(f"  {d:<6} " + " ".join(f"{_fmt(row[m]):>6}" for m in METRICS))


if __name__ == "__main__":
    main()
