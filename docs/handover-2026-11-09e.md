# Hand-over: the table pass ships on in the image (ADR-047) (2026-11-09e)

Read after `CLAUDE.md` and `STATUS.md`. The user accepted ADR-046's
recommendation and asked for it to be made so, with a PR. Previous:
[`handover-2026-11-09d.md`](handover-2026-11-09d.md) (the table pass itself).
Branch: `ccr-f0a0e598-etgjpb`. **No paid API tokens.** Nothing is deployed yet.

## What changed

- `backend/Dockerfile`: `ARG TABLES=1` installs CPU torch 2.15.0 / torchvision
  0.29.2 / `requirements-tables.txt` and runs `fetch_table_models.py` (reads the
  pinned ids from `parsers/tables.py` with `ast`, downloads `config.json` +
  `model.safetensors` per model into `HF_HOME=/opt/hf`, made world-readable in
  the same layer). Env: `WISEAU_TABLES=tatr`, `WISEAU_TABLE_THREADS=1`,
  `HF_HUB_OFFLINE=1`. `/opt/hf` is deliberately not chown-ed (that copied 230 MB
  into a second layer).
- `parsers/ocr.py`: `_MUPDF_OCR_LOCK` around `get_textpage_ocr` (pre-existing
  thread-safety bug; test `test_scanned_pages_convert_concurrently`); the table
  pass is wrapped so a failure keeps the page without it.
- `parsers/tables.py`: quiet transformers' unused-weights warnings; one
  `Table Transformer loaded` log line.
- CI (`backend-tests.yml`, docker-build job): an offline in-image table check and
  a lean-image (`TABLES=0`) build + conversion check.
- Docs: `docs/hosting.md` (§2 note, Cloud Run memory, §7 `WISEAU_TABLES`,
  Render, Spaces), `backend/README.md`, tech-spec config, ADR-047.

## Verified here

- Docker build of both images (with this sandbox's proxy CA added in a throwaway
  copy only; the committed Dockerfile needs nothing). Full 3.9 GB, lean 2.0 GB.
- `--network none` container, UID 1000: `/ping`, the CI table script, V5N1 p18
  over HTTP (14.7 s cold / 10.8 s warm, both tables), 4 concurrent (all OK, 26.9 s,
  peak 1.9 GB). Lean: converts, no tables.
- 568 tests pass, 8 skipped.

## Not verified

- A real HF Space / Cloud Run deployment (needs the user's accounts).
- The GitHub Actions run of the new CI steps (it runs on the PR).

## Next

1. Deploy the Space (`backend/README.md`), then `/metrics` + one scanned table.
2. Roadmap 3.1 heading hierarchy.
