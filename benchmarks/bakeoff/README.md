# Engine bake-off harness

Reproduces the 10-page engine comparison. The results and their write-up are in
[`docs/benchmarks/2026-11-02-bakeoff-rerun.md`](../../docs/benchmarks/2026-11-02-bakeoff-rerun.md).
Only `run_gemini.py` calls a paid API. It enforces a hard USD cap with a persistent
spend ledger and needs the user's explicit go-ahead before it is run. Nothing else here
calls an external service.

| Path | What |
|---|---|
| `regions.py` | The sample pages and the reading-order regions behind each reference. |
| `build_references.py` | Rebuilds the V16 references from the text layer. `--draft` writes Tesseract drafts of the scans. |
| `references/` | One `.txt` per page. Scans are hand-corrected from `references/drafts/`. |
| `run_engines.py` | Runs one engine over the sample, one page at a time. Writes `outputs/<engine>/`. |
| `outputs/<engine>/` | Raw Markdown per page and `timings.json`. |
| `run_gemini.py` | Gemini over the sample, under a hard budget (`--budget`, default $1). Use `--smoke` for one page. Ledger: `outputs/<model>/spend.json`. |
| `vote.py` | Word-level voting, consensus routers and the per-page oracle over the offline engines' outputs. No engines run. Write-up: `docs/benchmarks/2026-11-03-voting-experiment.md`. |
| `disagreements.py` | Lists words where engines disagree with a set-B reference, for adjudication against the page image. Log: `adjudication/log.md`. |
| `route.py` | The rule router (≥ 30% numbers or single column → Tesseract, else GLM-OCR). Writes `outputs/router-rule/`. |
| `survey.py` | Surveys image-only pages and makes the seeded stratified draw for a new held-out set. Set D's survey and draw: `survey/`. |
| `score.py` | Scores every engine in `outputs/` and writes `results.json`. |

Two samples: set A (`SAMPLE`, the original 10 pages) and set B (`SAMPLE_B`, 10 scanned pages blind-transcribed on 2026-11-03; see `docs/benchmarks/2026-11-03-set-b.md`). Add `--set b` to `run_engines.py` and `score.py` for set B, or `--set c` for set C (`SAMPLE_C`, held out for ADR-037 and the router; see `docs/benchmarks/2026-11-03-set-c.md`), or `--set d` for set D (`SAMPLE_D`, 12 scans from Vol 5 No 1 and No 3, held out for ADR-038; see `docs/benchmarks/2026-11-06-set-d.md`).

Engine names for the default path's history: `pymupdf` is whatever the default is now (ADR-038 since 2026-11-06), `tesseract-adr037` the path before it, `tesseract-adr035` the one before that, and `tesseract-unordered` the path before ADR-035. `tesseract-layout` is ADR-038 forced on.

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
