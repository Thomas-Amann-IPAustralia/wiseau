"""Kaggle GPU leg of the layout-region spike (roadmap 14 item 3.7b).

Runs on a Kaggle notebook with one T4, from a Kaggle dataset that holds
`layout_models.py`, `regions.py` and the test PDFs (`push.sh` builds it). It
answers what the CPU run in this repo cannot: does OvisOCR3 load and run on a
compute-capability-7.5 GPU, in which precision, at what VRAM and speed, and do
its regions match the CPU FP32 reference. Region *quality* is scored back in
the repo (`layout_regions.py`), from the files this writes.

Steps (each recorded, a failure in one never hides the others):
  1. Environment: nvidia-smi, Python and package versions, before anything is installed.
  2. Transformers route, Kaggle's own torch untouched: transformers + accelerate
     pinned to the repo's CPU run, then OvisOCR3 layout-only at fp16 on set S and
     set B, one page at a time, batch 1; bf16 and fp32 on one page as diagnostics;
     the structured prompt (regions with content) at fp16 on three dev pages.
  3. Optional (RUN_VLLM=1 at push time): the model card's vLLM 0.22.1 route in its own
     virtualenv, so its pinned torch never mixes with Kaggle's; one page.
All outputs land in /kaggle/working/outputs/, zipped as outputs.zip.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(os.environ.get("WISEAU_DATA", "/kaggle/input/wiseau-layout-spike"))
WORK = Path("/kaggle/working")
BENCH = WORK / "wiseau" / "benchmarks" / "bakeoff"
OUT = WORK / "outputs"
LOG = OUT / "kernel-log.jsonl"
PINS = ["transformers==5.22.1", "accelerate==1.15.0", "opencv-python-headless==5.0.0.93"]


def log(step: str, **kw) -> None:
    rec = {"step": step, **kw}
    print(json.dumps(rec)[:2000], flush=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def sh(step: str, cmd: list[str] | str, env=None, timeout=None) -> int:
    try:
        done = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                              env={**os.environ, **(env or {})}, timeout=timeout)
        log(step, cmd=cmd if isinstance(cmd, str) else " ".join(cmd), rc=done.returncode,
            stdout=done.stdout[-6000:], stderr=done.stderr[-6000:])
        return done.returncode
    except subprocess.TimeoutExpired:
        log(step, cmd=str(cmd), rc=None, error="timeout")
        return -1


def layout(engine: str, args: list[str], tag: str, timeout: int = 4 * 3600) -> int:
    return sh(f"{engine}:{tag}", [sys.executable, "layout_models.py", engine, "--device", "cuda",
                                  "--out", str(OUT / tag), *args], timeout=timeout)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # push.sh uploads one tar holding the repo layout the scripts expect
    # (wiseau/benchmarks/bakeoff/... and wiseau/pdf-test/...).
    # Kaggle unpacks an uploaded tar into the dataset, so the tree usually
    # arrives already extracted (…/wiseau/benchmarks/bakeoff/layout_models.py);
    # accept either form. The tree is copied because /kaggle/input is read-only.
    tars = sorted(Path("/kaggle/input").rglob("spike.tar")) if not (DATA / "spike.tar").exists() else [DATA / "spike.tar"]
    trees = sorted(p.parents[2] for p in Path("/kaggle/input").rglob("wiseau/benchmarks/bakeoff/layout_models.py"))
    if tars:
        shutil.unpack_archive(str(tars[0]), str(WORK), "tar")
    elif trees:
        shutil.copytree(trees[0], WORK / "wiseau", dirs_exist_ok=True)
        log("setup", source=str(trees[0]))
    else:
        log("setup", error="neither spike.tar nor an extracted wiseau/ tree found under /kaggle/input")
        return
    os.chdir(BENCH)

    sh("env:nvidia-smi", "nvidia-smi")
    sh("env:versions", f"{sys.executable} -m pip list 2>/dev/null | grep -i -E "
       "'^(torch|torchvision|transformers|accelerate|vllm|xformers|triton|pymupdf) '")
    sh("env:python", [sys.executable, "--version"])
    sh("install:transformers", [sys.executable, "-m", "pip", "install", "-q", *PINS, "pymupdf==1.28.0"])
    sh("env:versions-after", f"{sys.executable} -m pip list 2>/dev/null | grep -i -E "
       "'^(torch|transformers|accelerate|pymupdf) '")

    # 2. Transformers route. fp16 is the T4's native precision; bf16 is emulated.
    one = ["--set", "b", "--pages", "V2_p9"]
    layout("ovisocr3-layout", ["--dtype", "fp16", *one], "fp16")
    layout("ovisocr3-layout", ["--dtype", "bf16", *one], "bf16-diag", timeout=3600)
    layout("ovisocr3-layout", ["--dtype", "fp32", *one], "fp32-diag", timeout=3600)
    layout("ovisocr3-layout", ["--dtype", "fp16", "--set", "s"], "fp16")
    layout("ovisocr3-layout", ["--dtype", "fp16", "--set", "b"], "fp16")
    layout("ovisocr3-structured", ["--dtype", "fp16", "--set", "b", "--pages", "V2_p9,V4_p32,V2_p2",
                                   "--max-new-tokens", "16384"], "fp16")
    layout("pp-doclayoutv3", ["--dtype", "fp32", "--set", "s"], "fp32")  # the detector on GPU, for scale

    # 3. Optional: vLLM in its own environment (the card's route).
    if os.environ.get("RUN_VLLM") == "1" or (WORK / "wiseau" / "RUN_VLLM").exists():
        venv = WORK / "vllm-venv"
        if sh("vllm:venv", [sys.executable, "-m", "venv", str(venv)]) == 0:
            py = str(venv / "bin" / "python")
            if sh("vllm:install", [py, "-m", "pip", "install", "-q", "vllm==0.22.1", "pillow", "pymupdf==1.28.0"],
                  timeout=3600) == 0:
                sh("vllm:smoke", [py, str(BENCH / "kaggle" / "vllm_smoke.py"), str(OUT / "vllm")], timeout=3600)

    shutil.make_archive(str(WORK / "outputs"), "zip", OUT)
    log("done")


if __name__ == "__main__":
    main()
