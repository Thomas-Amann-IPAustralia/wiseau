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
| `score.py` | Scores every engine in `outputs/` and writes `results.json`. |

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
