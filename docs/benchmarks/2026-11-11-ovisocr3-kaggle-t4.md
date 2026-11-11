# OvisOCR3 on a Kaggle T4: the GPU leg of 3.7b (2026-11-11)

**Verdict: OvisOCR3 does not earn an opt-in.** On a T4, fp16 is correct and
gives the same regions as the CPU fp32 reference. Every page returns valid JSON,
and the set S scores are identical to the CPU row. But it takes **~50 s a page**
(median 52.8 s, max 122.8 s), against a bar of "well under 10 s", and the
shipped PP-DocLayoutV3 detector does the same job in 0.14 s on the same GPU.
Under `docs/kaggle-gpu-guide.md` Part 5 the experiment is closed. The CPU
detector (ADR-048) stays the only layout source. No production change.

## Run

- Kaggle notebook `thomasamann/wiseau-layout-spike-run` v3 (private), one Tesla
  T4 (15 GB), Kaggle torch 2.11.0+cu130, Python 3.13.16; pinned
  `transformers==5.22.1`, `accelerate==1.15.0`, `pymupdf==1.28.0`.
- Model `ATH-MaaS/OvisOCR3` @ `17f255e`, layout-only prompt, greedy, batch 1, 200 dpi.
- Raw outputs and the step log are committed in
  `benchmarks/bakeoff/outputs/kaggle-20261111/` (`kernel-log.jsonl`,
  `*.regions.json`).
- The first two pushes stopped at setup: Kaggle extracts an uploaded `.tar`, so
  `spike.tar` never existed in the notebook. `kernel.py` now accepts the
  extracted tree (commit `70c3983`).
- vLLM route (`RUN_VLLM=1`) not run.

## Results

| Check | Result |
|---|---|
| Loads on CC 7.5 | yes, 2.5–3.4 s load |
| fp16 | works: no NaNs, every page `finish_reason: stop`, valid JSON on all 26 pages |
| bf16 (emulated) | **OOM** on V2_p9 (tried to allocate 10.3 GiB; 12.6 GiB in use) |
| fp32 | works, V2_p9 50.5 s, 4.9 GB VRAM: no faster than fp16 |
| Speed, fp16 (26 pages) | mean 50.3 s, median 52.8 s, max 122.8 s (V16_p3, 2477 tokens); ~2.5–3× faster than 4-vCPU CPU |
| Peak VRAM, fp16 | 3.2–3.6 GB |
| Agreement with CPU fp32 (22 pages with a reference) | every region matches by category at IoU ≥ 0.9; 14 of 22 raw outputs byte-identical |
| Set S scores, regroup over `tables-tatr` v2 | **identical** to the CPU row (below) |
| Structured prompt, 3 pages | 41–87 s a page; for inspection only |
| PP-DocLayoutV3 on the T4, set S | 0.13–0.17 s a page (1.6 s first page) |

```
engine                                  docs  blk_f  typ_f  split  merge    ord   hd_p   hd_r  htree   teds
tables-tatr                               12  0.649  0.498  0.095  0.332  0.029  0.375  0.100  0.075  0.572
pp-doclayoutv3-regroup-tatr-v2            12  0.761  0.662  0.067  0.151  0.036  0.792  0.723  0.496  0.583
ovisocr3-layout-regroup-tatr-v2 (CPU)     12  0.741  0.631  0.065  0.158  0.031  0.756  0.742  0.487  0.578
ovisocr3-layout-t4fp16-regroup-tatr-v2    12  0.741  0.631  0.065  0.158  0.031  0.756  0.742  0.487  0.578
```

Set T was not re-run, on purpose. The fp16 regions match the CPU reference, so
set T's CPU numbers (OvisOCR3's better heading and caption labels) stand for
the GPU too.

## Why it is slow, and the one lever left

Time scales with output tokens, at ~20 tokens/s. OvisOCR3 writes every box as
pretty-printed JSON, about 50 tokens a region. Transformers also warned that
`flash-linear-attention` and `causal_conv1d` were missing, so the linear-attention
layers ran their PyTorch reference path. Those fused kernels (or vLLM) could
speed it up several times. Reaching ~0.15 s, the detector's figure, would need
over 300×. Even under 10 s a page, OvisOCR3 would be a 1–2 GB GPU dependency
bought for a modest labelling edge (set T hd_r 0.93 vs 0.78, captions 8 vs 4 of
13). Wiseau's deployment targets have no GPU. Reopen only if a GPU host is
planned *and* the heading/caption edge becomes the bottleneck.
