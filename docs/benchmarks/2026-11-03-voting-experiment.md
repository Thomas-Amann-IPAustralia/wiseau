# Voting across the offline engines: 2026-11-03

**Question:** we already run three self-hosted engines (PyMuPDF + Tesseract, docling
and GLM-OCR). Can combining their outputs beat each of them alone, before any money
is spent on Gemini labels or on training a model?

**Answer: no, not by voting.** Word-level majority voting is worse than GLM-OCR alone
on scans. The interesting result is a different one: picking the right *single*
engine for each page would get close to Gemini. No reference-free way of making
that pick that we tried works, though.

No paid APIs were used. Everything below is computed from the outputs already
committed in `benchmarks/bakeoff/outputs/`. Rerun it with
`python benchmarks/bakeoff/vote.py && python benchmarks/bakeoff/score.py` (takes
about 4 s).

## What was tried

All of it is in `benchmarks/bakeoff/vote.py`. Gemini is excluded as a voter: it is
the target to beat.

- **`vote-<pivot>` (ROVER-style word vote):**
  - Each engine's output is tokenised with the scorer's `tokens()`.
  - The other two engines are aligned to a *pivot* engine by word edit distance.
  - Each pivot word is kept unless the other two agree on something else, which
    can be a different word or nothing.
  - A word the pivot lacks is added only if both other engines inserted it in the
    same place.
  - A three-way split keeps the pivot's word, so the pivot works as the
    tie-breaker. Each engine was tried as the pivot.
- **`consensus-order` / `consensus-words` (reference-free router):** for each page,
  take the one engine whose output agrees most with the other two. Agreement is
  measured either by word edit distance (order counts) or by word recall (order
  ignored).
- **`oracle-best-single`:** for each page, take whichever engine scores best
  *against the reference*. This cheats, so it is not a method. It shows the ceiling
  of a perfect per-page router.

The vote's output is a plain token sequence, not Markdown. It measures whether
voting finds the right words. Rebuilding layout would be a separate problem, and
only worth solving if the words came out better.

## Results

The single-engine and Gemini rows are the same as on
[2026-11-03](2026-11-03-gemini-3.5-flash.md). The voting and router rows need all
three engines on every page, so their s/page is the sum of the three engines'
times.

| Method | Pages | wer ↓ | bow_r | bow_p | num_r | num_p | s/page |
|---|---|---|---|---|---|---|---|
| PyMuPDF + Tesseract — scanned | 6 | 0.648 | 0.930 | 0.945 | 0.845 | 0.908 | 5 |
| docling — scanned | 6 | 0.213 | 0.841 | 0.928 | 0.684 | 0.912 | 41 |
| GLM-OCR — scanned | 6 | 0.103 | 0.930 | 0.956 | 0.895 | 0.892 | 106 |
| vote, pivot PyMuPDF — scanned | 6 | 0.199 | 0.846 | 0.967 | 0.717 | 0.938 | 152 |
| vote, pivot docling — scanned | 6 | 0.183 | 0.853 | 0.947 | 0.703 | 0.951 | 152 |
| vote, pivot GLM-OCR — scanned | 6 | 0.127 | 0.891 | 0.963 | 0.772 | 0.919 | 152 |
| consensus-order — scanned | 6 | 0.172 | 0.853 | 0.953 | 0.719 | 0.878 | 152 |
| consensus-words — scanned | 6 | 0.278 | 0.972 | 0.961 | 0.899 | 0.902 | 152 |
| *oracle best single* — scanned | 6 | *0.028* | *0.988* | *0.987* | *0.947* | *0.946* | *64* |
| Gemini 3.5 Flash — scanned | 6 | 0.034 | 0.996 | 0.999 | 0.993 | 0.994 | 22 |

Born-digital pages are not worth voting on. PyMuPDF reading the text layer is
already exact (wer 0.019) at 0.2 s/page. Every vote and router scored the same or
worse on those pages, at about 1,000 times the cost.

Per scanned page (wer / bow_r / num_r):

| Page | Tesseract | docling | GLM-OCR | best vote (pivot GLM-OCR) | oracle picks |
|---|---|---|---|---|---|
| V2 p6 | 0.733 / 0.986 / 0.953 | 0.019 / 0.988 / 0.930 | 0.016 / 0.997 / 0.953 | **0.011** / 0.989 / 0.930 | GLM-OCR |
| V2 p17 (typewriter table) | **0.036** / 0.982 / 0.963 | 0.704 / 0.559 / 0.421 | 0.462 / 0.621 / 0.607 | 0.435 / 0.646 / 0.626 | Tesseract |
| V2 p20 | 0.658 / 0.989 / 0.964 | 0.033 / 0.978 / 0.982 | 0.032 / 0.994 / 0.982 | **0.022** / 0.989 / 0.982 | GLM-OCR |
| V4 p7 | 0.794 / 0.894 / 0.653 | 0.325 / 0.678 / 0.408 | **0.029** / 0.995 / 0.980 | 0.151 / 0.863 / 0.653 | GLM-OCR |
| V4 p11 | 0.905 / 0.736 / 0.558 | 0.171 / 0.855 / 0.423 | **0.027** / 0.973 / 0.865 | 0.129 / 0.871 / 0.500 | GLM-OCR |
| V4 p26 | 0.761 / 0.993 / 0.980 | 0.025 / 0.989 / 0.939 | 0.054 / 0.996 / 0.980 | **0.011** / 0.989 / 0.939 | docling |

## Findings

1. **Voting makes the best engine worse.**
   - With GLM-OCR as the pivot, scanned wer goes from 0.103 to 0.127, word recall
     from 0.930 to 0.891, and number recall from 0.895 to 0.772.
   - The cause is pages where one engine is right and the other two are wrong in the
     same places. On V4 p7, GLM-OCR scores 0.029, but Tesseract and docling both miss
     the same passages, so together they outvote it and the vote scores 0.151.
   - Voting only helps when errors are independent and every voter is fairly good.
     These three engines fail together on the hard pages.
2. **Voting helps a little, but only on easy pages.** Where all three engines read
   the page well (V2 p6, V2 p20, V4 p26), the vote lowers wer to about 0.01–0.02. It
   still loses a few numbers there, for example V2 p6 number recall 0.953 → 0.930.
   That is a small gain on pages that are already fine, and running all three
   engines costs about 150 s/page.
3. **Tesseract's words are good, but its order is wrong.** On scans it finds 93% of
   words, the same as GLM-OCR. Its wer is 0.65–0.9 because it reads straight across
   both columns. A scrambled order lines up badly with the other engines, so it
   mostly adds noise to the vote.
4. **Picking the right engine per page would nearly match Gemini.**
   - The oracle reaches scanned wer 0.028, against Gemini's 0.034, at no API cost.
   - Its number recall, 0.947, is still well below Gemini's 0.993.
   - No single engine is best on every page: GLM-OCR on 3 pages, docling on 1, and
     Tesseract on the typewriter table.
5. **But agreement between engines doesn't tell you which one to pick.** The
   reference-free routers chose the oracle's engine on only 5 (`consensus-order`) or
   6 (`consensus-words`) of the 10 pages, and both score worse than GLM-OCR alone
   on scans. Two engines that are wrong together look like a consensus.

## What this means for the next step

- **Drop the voting ensemble.** It costs three engines' time and lowers fidelity on
  the pages that matter.
- **The headroom is in routing**, choosing one engine per page (option C in the
  2026-11-03 discussion). To work, the router has to judge the *page itself* (layout,
  table density, typewriter versus print), since agreement between outputs is the
  wrong signal. It then runs only the one engine it picks, so it is also the cheap
  option. A small classifier on page images or simple layout features is the natural
  fit. Training labels would be "which engine scored best on this page", which needs
  references or Gemini transcriptions for a few hundred varied pages.
- This doesn't change the case for fine-tuning GLM-OCR on Gemini-labelled pages
  (option B). The two can be combined.

## Caveats

- **Tiny sample:** 6 scanned pages from 2 journals. The oracle and router numbers
  come from picking among 3 engines on 6 pages, so they are noisy and optimistic.
  Treat them as a direction, not a measurement.
- **The references may favour Tesseract.** The scanned references were hand-corrected
  from Tesseract drafts (`build_references.py --draft`, 300 dpi, psm 6). V2 p17 is a
  single full-page region, so Tesseract's reading order there probably matches the
  reference's by construction. Its 0.036 on the typewriter table should be checked
  against an independent transcription before anyone builds a router on it.
- The vote is a simple ROVER. Weighting engines by page type, or using per-word
  confidence scores (which these outputs don't carry), could do better. But
  finding 1 is about correlated errors, and neither of those changes fixes that.
