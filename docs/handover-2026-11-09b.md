# Hand-over: Tier 2.1–2.2 — the document IR and its byte-neutral serializer (2026-11-09b)

Read this after `CLAUDE.md` and `STATUS.md`. The plan is still
[`roadmap-ocr-and-structure.md`](roadmap-ocr-and-structure.md): Tiers 0 and 1 are
done, **Tier 2.1–2.2's core is done (ADR-043)**, and 2.3 (the structural gold set)
is next. The previous hand-over, [`handover-2026-11-09.md`](handover-2026-11-09.md),
still holds the environment rebuild steps (Tesseract, PaddleOCR, Table Transformer
venvs) and the Tier 1 findings. Branch: `ccr-f0a0e598-etgjpb`.

**Standing user instruction: no paid API tokens** (Gemini, OpenAI) until the user
says otherwise. Nothing in this session used one.

## What happened

- **`parsers/document.py`** — the IR. `Document(pages, elements, relations)`;
  `Element(type, text, source, text_format, bbox, page, id, lines, rows, level,
  signal, confidence)`. `text` is what the engine produced and is never edited;
  inferred structure goes in its own fields with the `signal` that inferred it.
  `to_dict()` is deterministic (schema `"0.1"`, coordinates to 0.01).
- **`parsers/serializer.py`** — `to_markdown(doc)` (fixed joins) and
  `split_markdown(md, source)` (an engine's Markdown cut into typed top-level
  blocks, never inside a code fence; `signal="markdown-syntax"`).
- **Adapters.** `file_parser.file_to_document()` builds the IR for every path;
  `file_to_markdown` = `clean_markdown(to_markdown(file_to_document(...)))`.
  - Text-layer pages: PyMuPDF4LLM Markdown, per page, typed blocks, **no boxes**.
    The fast path makes one call with `page_chunks=True` (headings are sized over
    all pages, so per-page calls would change output); the mixed path still calls
    per page, as before.
  - OCR pages: `TesseractEngine.read_elements()` returns psm 3 `text` blocks and
    ADR-038's row-stitched `table`s (cells = OCR lines, with boxes), or the guard:
    ADR-035 paragraphs (`tesseract-geometric`) or MuPDF's own text (`mupdf-ocr`).
    `ocr_page()` is now a render of `read_elements()`. Other engines fall back to
    one `text` element via `OcrEngine.read_elements`.
  - Mammoth, docling, GLM-OCR: Markdown split into blocks with `page = None`.
- **Structure exposed, strings kept.** `reading_order.order_paragraphs()` and
  `tesseract_layout.groups()` / `read_page_blocks()` return what `order_lines()`,
  `render()` and `read_page()` used to flatten; those still exist as renders.
- **Gate: byte-for-byte identical** — 15 whole PDFs (859 pages; fast, mixed and
  all-OCR paths), a synthetic interleaved mixed PDF, all 76 scored pages. Time
  unchanged. Write-up: `docs/benchmarks/2026-11-09-ir-neutrality.md`.
- Tests: 548 passed, 8 skipped (`tests/test_document_ir.py` is new, including a
  2,000-case property test of the split/join round trip).

## Harness added or changed

| Script | What |
|---|---|
| `whole_docs.py BACKEND OUT PDF...` | Whole-document conversion with any checkout's default path. Use it with a `git worktree` of the base commit for any refactor that claims neutrality. |
| `run_engines.py pymupdf` | Also writes `<page>.json`, the page's IR (committed for the 76 scored pages). |
| `run_engines.py tesseract-unordered` | Now patches `ocr.order_paragraphs` (was `order_lines`). |

## Pitfalls found

- **PyMuPDF4LLM sizes headings from all the pages it is given.** Never replace
  the fast path's single call by per-page calls (output changes); `page_chunks=True`
  is the way to keep pages apart.
- Every nonempty `page_chunks` text ends in a blank line and none starts with
  whitespace on V16 and V20 (checked); that is why stripping and re-joining pages with
  `"\n\n"` is neutral there. A PDF that breaks this would differ only in
  whitespace the normalizer may not fold; the whole-document gate is the check.
- A whitespace-only document now normalizes to `""` (was `"\n"`); both are "no
  text" to `main.py` (`markdown.strip()`).
- 382 of the 859 corpus pages are OCR'd under ADR-040 (earlier notes counted 336
  *scanned* pages over "16 PDFs"; the corpus in `pdf-test/` is 15 PDFs).

## What to do next, in order

1. **2.3 Structural gold set.** Annotate 10–15 documents' structure (type, text,
   parent, order, page, relations). The committed `outputs/pymupdf/<page>.json`
   files are a starting draft for the scanned pages: element boxes and order are
   there to correct, not to copy blind (references stay blind for *text*).
2. **Box-bearing text-layer adapter** (`page.get_text("dict")` spans matched to
   PyMuPDF4LLM's blocks), under the same byte-for-byte gate. Needed before
   furniture (3.2) and continuation (3.3) can use geometry on born-digital pages.
3. **The explicit Markdown dialect** (pipe tables / HTML for merged cells): the
   first deliberate output change through the serializer; its own ADR, scored on
   all sets (wer should not move; the scorer strips HTML).
4. **3.7 region routing with TATR** as an IR pass over `table`/`text` elements
   (prose-vs-table check designed on A–D and L, held out on E and F).
