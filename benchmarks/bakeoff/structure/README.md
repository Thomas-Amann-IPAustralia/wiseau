# Set S: the structural gold set (roadmap 14 item 2.3)

Twelve documents, 20 pages (399 blocks, 43 relations), annotated for **structure**: what each block is, how
headings nest, what tables contain cell by cell, what is bold or italic, and how
blocks relate (captions, continuations, footnotes). `structure.py` scores engine
outputs against it; `score.py` still scores the words.

## Protocol

- **Blind.** Each page was annotated from its page image (rendered at 100 dpi, with
  150 dpi crops of dense tables), never from an engine's output. The *text* comes
  from the page's frozen blind reference (`references/<page>.txt`, sets A–F), or
  for the born-digital pages without one (V16 p3, p97) from the PDF's own text
  layer. Every gold file's words match its reference token for token (checked
  with `score.tokens`); the one difference is V16 p97, whose text layer glues
  footnote markers to the words they mark (`Argentina2`).
- **Frozen before scoring.** The files were committed before `structure.py` was
  run on them. (The scorer was tried once on S01 while the format was being
  settled; no engine output was opened.)
- **The ideal Markdown.** A gold file is what the pages *should* convert to:
  headings with levels, paragraphs joined across columns, tables cell by cell,
  bold and italic as printed, footnotes as `[^n]`. The user's review of the
  frontier-model transcriptions (`docs/Frontier-transcription-check.md`) asked
  for exactly this: text recognition is good enough; structure is what is missing.

## The documents

| Doc | Pages | Why it is here |
|---|---|---|
| S01 | V5N1 p18–20 | Side-by-side table panels; a gel figure with a key and caption; descriptions continuing across columns and pages; two more comparison tables (user's frontier check) |
| S02 | V4 p19 | A five-variety table above two columns of prose (user's check) |
| S03 | V2 p11–12 | Three photos with captions; a wide four-variety table with wrapped cells (user's check) |
| S04 | V4N1 p7 | Four heading levels, two columns, a figure interrupting a paragraph, a table, 200 dpi (user's check) |
| S05 | P63a p7 | A 1963 typewritten patent page with a running header (user's check) |
| S06 | V4 p25–27 | Many short entries under species headings, a header-less two-column list, a fee table, a directory as a list |
| S07 | V4 p22–23 | Two comparison tables, a paragraph continuing across pages, an objections section with a lettered list |
| S08 | V2 p20–21 | A table continuing onto the next page; nested numbered and lettered corrigenda |
| S09 | V5N3 p23–24 | Four heading levels, a fee table with section rows, a paragraph continuing across pages, an address directory |
| S10 | V16 p3 | **Complex contents page** (born-digital): masthead, two contents tables, ten captioned staff photos, page furniture |
| S11 | V16 p97 | **Footnote-heavy page** (born-digital): 53 entries with 69 footnote markers, five footnotes |
| S12 | V16 p90 | A born-digital directory table whose second column of the page continues the table |

**Gaps.** The test corpus has no deeply nested report (four levels is the most,
S04 and S09) and no table that continues across pages *with a repeated header*;
S08's continues without one. These need material the corpus does not have.

## Format

Each file is Markdown, split into blocks at blank lines (`serializer.split_markdown`).

- `<!-- page N -->` on its own starts PDF page N (1-based, as in the file).
- A block may start with one annotation line, `<!-- TYPE key=value ... -->`:
  - `TYPE` sets a type Markdown has no syntax for: `title`, `caption`,
    `footnote`, `page_header`, `page_footer`, `page_number`. Without it the type
    is read from the Markdown (`heading` with its level, `table`, `list`,
    `paragraph`, ...).
  - `id=X` names the block; relations name their target by id:
    `caption_of=` (a table's or figure's caption, including a table's
    "(* = variety used for comparison)" note), `continues_from=` (a paragraph
    continuing one on an earlier page), `continues_table=` (a table continuing
    one, as a side-by-side panel or on the next page), `footnote_for=`.
- `<!-- figure id=X -->` alone is a photograph or drawing (no text).
- Tables are pipe tables when the first printed row is a header, else HTML
  `<table>` without a header (no invented header text). A row label printed on
  its own line above its values ("AWN LENGTH") is a row with the label in the
  first cell and the rest empty; merged label cells are not modelled.
- A paragraph broken by a column change is one paragraph; one broken by a page
  change is two blocks, the second with `continues_from=`.
- Heading levels are relative: the scorer compares the rank of each level within
  the document, so `#`/`##` and `##`/`###` agree.
- Text keeps the reference's line breaks and hyphenation (`pig-\nmented`); the
  scorer works on words, so this costs nothing.
