# Kaggle GPU leg of the layout-region spike (roadmap 14 item 3.7b)

**Step-by-step setup (accounts, token, two ways to run it):** [`docs/kaggle-gpu-guide.md`](../../../docs/kaggle-gpu-guide.md).

The CPU leg ran in the repo (`docs/benchmarks/2026-11-10-layout-regions.md`).
This leg answers what a CPU can't: whether OvisOCR3 loads and runs on a free
Kaggle **T4** (compute capability 7.5, no native BF16), in which precision, at
what VRAM and speed, and whether its fp16 regions match the CPU fp32 reference.
Region quality is scored back in the repo with `layout_regions.py`. Nothing
here changes production.

## What runs (`kernel.py`)

1. Environment: `nvidia-smi`, Python and package versions, before any install.
2. **Transformers route**, leaving Kaggle's own torch untouched. It pins
   `transformers==5.22.1` and `accelerate==1.15.0`, the versions the CPU leg
   used. OvisOCR3 runs layout-only at **fp16**, one page at a time with batch
   1, on set S and set B. bf16 and fp32 each run on one page (V2 p9) as
   diagnostics. The structured prompt runs at fp16 on three dev pages, and
   PP-DocLayoutV3 on set S for scale.
3. Optional: the card's **vLLM 0.22.1** route, in its own virtualenv so its torch
   never mixes with Kaggle's, on one page. Set `RUN_VLLM=1` when pushing.

Every step is logged to `outputs/kernel-log.jsonl` with its return code and
output tail. A failing step never hides the others.

## How to run it

```bash
pip install kaggle                       # plus API credentials (kaggle.json / KAGGLE_KEY)
KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push.sh            # data + kernel, both private
kaggle kernels status <user>/wiseau-layout-spike-run                # wait for "complete"
KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push.sh fetch      # outputs -> outputs/kaggle-<date>/
```

Then unzip, **commit the outputs straight away** (post-mortem 2026-11-01), and
score them:

```bash
cp -r outputs/kaggle-<date>/fp16/ovisocr3-layout outputs/ovisocr3-layout-t4fp16   # any engine dir name works
python layout_regions.py build ovisocr3-layout-t4fp16 --base tables-tatr
python layout_regions.py score ... ovisocr3-layout-t4fp16-regroup-tatr
```

**What gets uploaded:** the two harness scripts and `pdf-test/` (public PBR
journals and patent specifications), as a private Kaggle dataset. No paid API is
called. The run uses free GPU quota (expect about an hour or less).

## Known risks to read in the log

- **fp16 overflow.** The weights are BF16, and `mamba_ssm_dtype` is float32 for
  the linear-attention state. NaNs or empty output at fp16 with sane output at
  fp32 mean the T4 needs fp32 (slow) or the model is not T4-viable.
- **No fused kernels.** Without `flash-linear-attention` / `causal_conv1d`,
  Transformers uses its PyTorch reference path: correct but slow. Their wheels
  target newer GPUs, so they aren't installed.
- **vLLM on CC 7.5.** Check the release notes for 0.22.1. A refusal here is a
  result, not something to patch.
