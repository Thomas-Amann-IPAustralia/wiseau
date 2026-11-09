# Frontier transcription check: Claude's references vs GPT-6 Astra and Gemini 4 Argon (2026-11-09)

**The page to read is
[`2026-11-09-frontier-transcriptions.html`](2026-11-09-frontier-transcriptions.html)**
(self-contained; open it in a browser). It shows each page image beside the three
transcriptions.

## Why

The user pointed out that Claude Opus 5.5 scores poorly on OCR leaderboards
(OmniDocBench v1.6), while every scanned reference in this benchmark was typed by
Claude. If those references are wrong, every wer in `results.json` is measured
against a flawed answer key. The user asked for the five hardest pages Claude has
annotated to be transcribed "by hand" by the flagship OpenAI and Google models, with
the outputs left untouched, so the user can judge which model reads these pages best.
Paid API use was approved in the same request.

## What was done

- **Pages.** The five hand-transcribed scans with the highest *median* wer across every
  engine already scored on them (oracle, consensus and hidden-layer rows left out). The
  rule was fixed before any call: V5N1 p18 (side-by-side tables, 0.313), V4 p19 (table
  above prose, 0.194), V2 p12 (wide table, 0.172), V4N1 p7 (two columns + data at
  200 dpi, 0.161), P63a p7 (1963 typewritten patent, 0.145).
- **Models.** `gpt-6-astra` (OpenAI calls it its flagship) and `gemini-4-argon` (Google:
  "our latest, most capable model"), both from the providers' model pages on 2026-11-09.
  GPT: reasoning effort high, image detail `original`. Gemini: thinking level high,
  image `media_resolution` ULTRA_HIGH (its maximum). One prompt for both, which states
  the conventions Claude's references were typed under (`run_frontier.py`, `PROMPT`).
- **Claude's column** is the committed reference, unchanged, not a new attempt.
- **Integrity.** Raw response bodies are stored byte for byte before parsing, with
  SHA-256 hashes in `benchmarks/bakeoff/outputs/frontier-check/manifest.json`. The HTML
  embeds each text's exact bytes, and the browser re-hashes them on load against the
  recorded hash (all 15 verified in Chromium). Display options (rendered Markdown, the
  disagreement highlight, blind mode) never change the text.
- **Cost.** $2.63 for the answers received. The ledger books $4.98 because four
  requests that got no answer are counted at their worst case: three proxy 502s (this
  session's egress proxy drops a request that is silent for 30 s) and one runner time
  limit. Each was sent again; no page has more than one answer per model to choose from.
  One Gemini answer (V5N1 p18 at the "high" image setting) was superseded by the
  ULTRA_HIGH run, for parity with GPT's full-resolution read, before any comparison. The
  two differ by one blank line. It is kept in `raw/`.

## What the outputs show (facts, not a verdict)

Claude is one of the three being judged here, so this section lists where the texts
differ and does not rule on who is right. Counting words regardless of order and
Markdown table syntax, the three transcriptions are nearly identical:

| Page | Words (Claude / GPT / Gemini) | Words found in one transcription and neither of the others |
|---|---|---|
| V5N1 p18 | 358 / 358 / 358 | Claude: 32, all straight quotes (`'Grasslands`) where both models print curly ‘ ’ |
| V4 p19 | 590 / 591 / 588 | GPT: en dashes `–` in the 21 ranges and curly-quoted variety names, where Claude and Gemini both have em dashes `—` and straight quotes; also `sec:`, `on`. Gemini: `yellow-green` ×2, `No.` |
| V2 p12 | 450 / 450 / 450 | GPT: `55C` (Claude and Gemini: `55D`), `2.7-3.5` (Claude and Gemini: `2.7-3.6`) |
| V4N1 p7 | 611 / 611 / 611 | none |
| P63a p7 | 379 / 379 / 380 | Gemini: `10` (a margin number) |

Spots to check against the image: V2 p12's outer-petal border colour (55C/55D) and
outer petal width range (3.5/3.6); V4 p19's range dashes and quote marks; V5N1 p18's
quote marks; P63a p7's margin `10`. Layout differs more than the words do: both models
use Markdown tables where Claude's references are plain lines, and GPT laid V5N1 p18's
side-by-side tables out as one wide table. `score.py`'s order metric would read that as
an error against a reference that reads one table and then the other.

Set D (V5N1) and set C (V2, V4) were typed before ADR-039, when scanned references
recorded most curly quotes as straight (STATUS 2026-11-08), so the quote differences
were expected there.

## Files

`benchmarks/bakeoff/run_frontier.py` (the calls), `benchmarks/bakeoff/build_frontier_html.py`
(the page), `benchmarks/bakeoff/outputs/frontier-check/` (images, answers, raw bodies,
manifest).
