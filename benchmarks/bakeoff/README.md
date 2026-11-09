# Engine bake-off harness

Reproduces the 10-page engine comparison. The results and their write-up are in
[`docs/benchmarks/2026-11-02-bakeoff-rerun.md`](../../docs/benchmarks/2026-11-02-bakeoff-rerun.md).
Only `run_gemini.py` and `run_frontier.py` call paid APIs. It enforces a hard USD cap with a persistent
spend ledger and needs the user's explicit go-ahead before it is run. Nothing else here
calls an external service.

| Path | What |
|---|---|
| `regions.py` | The sample pages and the reading-order regions behind each reference. |
| `build_references.py` | Rebuilds the V16 references from the text layer. `--draft` writes Tesseract drafts of the scans. |
| `references/` | One `.txt` per page. Scans are hand-corrected from `references/drafts/`. |
| `run_engines.py` | Runs one engine over the sample, one page at a time. Writes `outputs/<engine>/`. |
| `outputs/<engine>/` | Raw Markdown per page, `timings.json`, and `runs.json` (seconds, peak RSS, failures; runs since 2026-11-08). The `docling` engine also writes `<page>.json`, its document tree. |
| `run_gemini.py` | Gemini over the sample, under a hard budget (`--budget`, default $1). Use `--smoke` for one page. Ledger: `outputs/<model>/spend.json`. |
| `vote.py` | Word-level voting, consensus routers and the per-page oracle over the offline engines' outputs. No engines run. Write-up: `docs/benchmarks/2026-11-03-voting-experiment.md`. |
| `disagreements.py` | Lists words where engines disagree with a set-B reference, for adjudication against the page image. Log: `adjudication/log.md`. |
| `route.py` | The rule router (≥ 30% numbers or single column → Tesseract, else GLM-OCR). Writes `outputs/router-rule/`. |
| `survey.py` | Surveys pages (OCR'd from their images, hidden layers ignored) and makes the seeded stratified draw for a new held-out set. Records native dpi; `--split-dpi` stratifies on it, `--exclude` drops pages. Set D's survey and draw: `survey/`. |
| `score.py` | Scores every engine in `outputs/` and writes `results.json`. Metrics are listed below. |
| `preprocess.py` | Pixel variants for the preprocessing ablation (engines `pre-ident`, `pre-deskew`, `pre-norm`, `pre-sauvola`, `pre-denoise`; `ident` is the pixel-exact control) and per-page features (`survey/preprocess-features.json`). Write-up: `docs/benchmarks/2026-11-09-preprocessing-ablation.md`. |
| `ml_engines.py` | Recogniser swaps on Tesseract's psm 3 layout (`rec-MODEL` by line, `wrec-MODEL` by word; `rec-tesseract` is the control), Table Transformer (`tatr`) and `ppstructure-v3`. Each needs its own venv; see `docs/handover-2026-11-09.md`. Write-up: `docs/benchmarks/2026-11-09-recogniser-and-table-bakeoff.md`. |
| `compare.py` | Paired per-page comparison of engines against a control, by set (`--sets`), by scan type (`--by-type`), changed pages (`--pages`). |
| `rule_search.py` | Searches per-page preprocessing rules on the development sets and scores the best on E and F. |
| `guard_cache.py`, `guard_eval.py` | The two texts the ADR-038 guard chooses between, for any pages; which pages a counting rule flips and whether that helps (ADR-042). |
| `tatr_detections.py` | Table Transformer's raw table boxes and scores per scored scan. |
| `run_frontier.py` | **Paid.** The user-requested frontier check (2026-11-09): GPT-6 Astra and Gemini 4 Argon transcribe the five hardest hand-annotated pages. Raw bodies kept byte for byte, hashes in `outputs/frontier-check/manifest.json`. |
| `build_frontier_html.py` | Builds `docs/benchmarks/2026-11-09-frontier-transcriptions.html`: page image beside Claude's reference and both models' answers, hash-verified in the browser. |
| `word_errors.py` | Lists every word an engine misread, lost or inserted on a set, against the references (`ENGINE --set l [--summary]`). Says *what* went wrong where `score.py` says how much. |

Two samples: set A (`SAMPLE`, the original 10 pages) and set B (`SAMPLE_B`, 10 scanned pages blind-transcribed on 2026-11-03; see `docs/benchmarks/2026-11-03-set-b.md`). Add `--set b` to `run_engines.py` and `score.py` for set B, or `--set c` for set C (`SAMPLE_C`, held out for ADR-037 and the router; see `docs/benchmarks/2026-11-03-set-c.md`), or `--set d` for set D (`SAMPLE_D`, 12 scans from Vol 5 No 1 and No 3, held out for ADR-038; see `docs/benchmarks/2026-11-06-set-d.md`). Sets E (`SAMPLE_E`, 12 patent/WO pages), F (`SAMPLE_F`, 14 held-out PBR pages) and L (`SAMPLE_L`, 8 random pages from the Acrobat-OCR'd PBR journals) take `--set e`, `--set f` and `--set l`; see `docs/benchmarks/2026-11-08-layer-audit.md`.

Engine names for the default path's history: `pymupdf` is whatever the default is now (ADR-038 since 2026-11-06), `tesseract-adr037` the path before it, `tesseract-adr035` the one before that, and `tesseract-unordered` the path before ADR-035. `tesseract-layout` is ADR-038 forced on. `no-borders` is the default before ADR-041 (scanner bands OCR'd as they are). `trust-layer` is the default before ADR-040 (a scan's invisible OCR layer read as its text). For the text-layer audit: `hidden-layer` is a page's own text layer, as is (no OCR, no Markdown), and `forced-ocr` is the default path with `WISEAU_OCR_MODE=force`.

Run every command from the repo root:

```bash
python benchmarks/bakeoff/run_engines.py pymupdf      # backend requirements + tesseract
python benchmarks/bakeoff/run_engines.py docling      # pip install docling==2.145.* easyocr
python benchmarks/bakeoff/run_engines.py glm-ocr      # glm-ocr/service.py on :7860
python benchmarks/bakeoff/score.py
```

Run the engines one after another, never side by side. Concurrent runs on a small box
inflate every timing.

To compare a new engine or setting, add its outputs under `outputs/<name>/`, add the
name to `ENGINES` in `score.py`, and re-score. The references stay fixed, so the new
numbers line up directly with the committed ones.

## Metrics (2026-11-08, roadmap 14 item 0.3)

`score.py` reports, per page and as group means:

| Metric | What it measures | Better |
|---|---|---|
| `wer` | Word edit distance / reference words. Order counts. | lower |
| `cer` | Character edit distance / reference characters, on the same normalised words joined by spaces. Sees a misread letter that `wer` counts as a whole wrong word. | lower |
| `ord` | Line reading-order error. Each reference line of 3+ words is located in the output by trigram voting (at least half its trigrams must agree on where it starts). `ord` is the edit distance between the located lines in reference order and in output order, divided by the lines located. References mark lines, not blocks, so this stands in for block order until roadmap 2.3 annotates blocks. A page with under two locatable lines has no `ord` and is left out of the mean. | lower |
| `bow_r` / `bow_p` | Word recall / precision, ignoring order. The gap between `wer` and `1 - bow_r` is mostly order. | higher |
| `num_r` / `num_p` | The same for tokens that contain a digit. | higher |
| `pun_r` / `pun_p` | Recall / precision of punctuation characters exactly as printed (NFC, ’ ≠ '), with Markdown syntax removed first. This is what ADR-039 is judged on. `results.json` also has `pun_rf`, the recall with ‘’“”–—… folded to ASCII, which separates "kept the mark" from "kept its printed form". | higher |
| `s/page` | Mean seconds per page. | lower |
| `rss` | Peak resident memory, MB: the high-water mark of the runner process or of its largest child (the `tesseract` CLI). It is a high-water mark for the whole run so far, not per page, and an HTTP engine's server is not visible. `-` for runs before `runs.json`. | lower |
| `fail` | Share of attempted pages that raised. A failed page is scored as empty output. `-` for runs before `runs.json`. | lower |

`wer`, `bow_*` and `num_*` are unchanged from earlier write-ups, so old and new
numbers compare directly. `edit_distance` is Myers' bit-parallel algorithm, which
gives exactly the textbook dynamic programme's answer (`_edit_distance_dp` is kept
as the oracle) and makes `cer` affordable.

**Caveat on `pun_*` for scans.** Scanned references were typed from page images,
some at 150 or 200 dpi, where ‘ and ' can be hard to tell apart, so their
punctuation records the transcriber's reading. Born-digital references are built
from the text layer and record the printed characters exactly.
