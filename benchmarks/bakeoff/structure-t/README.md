# Set T: a second, held-out structural gold set (roadmap 14 item 3.7b)

Fourteen single pages, annotated for structure in set S's format
([`../structure/README.md`](../structure/README.md): ideal Markdown, one block
per blank-line chunk, annotation comments for types and relations Markdown
lacks). Set T exists because set S was looked at while the layout-region pass
was built (`docs/benchmarks/2026-11-10-layout-regions.md`), so that pass needs
pages nobody tuned against.

## Protocol

- **Drawn by seed** (`../survey/set-t/draw.py`, seed 20261110, committed in
  `13061d7` before any annotation). The draw takes scanned pages with a frozen
  blind text reference (sets B–F, L) and leaves out three groups: set S's
  pages; set A's (whose references were corrected from Tesseract drafts); and
  V2 p9, whose layout regions were inspected in the CPU smoke test.
  - 10 PBR journal pages: 3 two-column prose, 3 two-column + data, 2 tables,
    2 single column.
  - 4 other publications, one per set E group (1963 and 1985 typewritten
    patents, a 2000 typeset patent, a 2006 WO publication).
- **Blind.** Each page was annotated from its page image (100 dpi; 160 dpi for
  the table pages), never from any engine's output. The text is the page's
  frozen reference (`../references/<page>.txt`).
- **Words unchanged.** `check.py` confirms that every file's words equal its
  reference's as a multiset, as set S's do. 9 files are in the same order. In 5
  the order differs only where the ideal Markdown reads differently: wrapped
  table cells, and a table's label/value columns.
- **Self-check.** Scoring each file against itself is perfect on 13 of 14. On
  T11 the page number "- 20 -" reads as a Markdown list item: the same quirk
  as S05's "- 7", left as printed.
- **Frozen before any engine ran on these pages for the layout pass.** Models,
  adapter settings and both variants (`layout_regions.py`) were fixed and
  committed before set T was scored.

## Annotation decisions (beyond set S's README)

- A row label printed on its own line above a values line (`THORN SHAPE—UPPER
  SIDE`, then `flat concave deep concave`) is a label row with empty cells, then
  a values row whose first cell is empty.
- Two-level row labels (V2N2 p32: `TERMINAL LEAFLET LENGTH` × `Mean / Range /
  Std deviation`) are two label columns. The outer label appears only on its
  first row; merged cells are not modelled.
- A misprinted cell kept as printed: V4 p8 `range13-18`. The label and first
  value are glued in the print and in the reference, so the glued token is the
  label cell and that row's first value cell is empty.
- Header-less two-column lists (variety | application number) and the contact
  numbers block are HTML `<table>`s, as in S06.
- Gel-lane labels beside a figure (V2N2 p31) are a list after the figure, as in
  S01. Words inside photographs are not in the references, so not here.
- Typed margin line numbers stay at the start of their line, inside list items
  too (WO06 p4).

| Doc | Page | Group | What it has |
|---|---|---|---|
| T01 | V2N2 p4 | two-column prose | Part title, 8 section headings, paragraphs across a column break, bold/italic runs |
| T02 | V5N3 p12 | two-column prose | A continued table, species heading, two variety entries, a comparison table with label rows |
| T03 | V2N2 p31 | two-column prose | Variety entry sections, a gel figure with lane labels and caption |
| T04 | V4 p14 | two-column + data | Species heading, photo with caption, 4-column comparison table with RHS sub-rows |
| T05 | V4 p8 | two-column + data | Paragraphs, a long disease/measurement table, two species entries |
| T06 | V4 p10 | two-column + data | 7-column comparison table, references section, species heading |
| T07 | V5N3 p19 | tables | A continued table, a lettered section with 5 species groups of entries |
| T08 | V2N2 p32 | tables | Two-level-label table, lettered section, species groups, a two-column list |
| T09 | V4N1 p27 | single column | Withdrawn/provisional lists (37 rows), variations, corrigenda by species |
| T10 | V2N2 p3 | single column | Editorial page: photo, signature and address blocks, contact-number table |
| T11 | P63b p20 | 1963 typewritten | Patent text with margin line numbers, running number, page number |
| T12 | P85 p6 | 1985 typewritten | Underlined title, indented paragraphs, docket footer |
| T13 | P00 p4 | 1997–2000 typeset | Bold title, two underlined section headings, page "1a" |
| T14 | WO06 p4 | 2006 WO | Running header, bulleted list with line numbers, bold sub-heading |
