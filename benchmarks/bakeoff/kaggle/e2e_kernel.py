"""Kaggle kernel: is a GPU worth it, end to end? (CPU vs GPU, one T4 machine)

Runs on a Kaggle notebook with one T4, from the private dataset `push_e2e.sh`
builds (the backend, glm-ocr/, the harness and the test PDFs). Every leg is a
whole conversion through the backend (`e2e.py`), CPU then GPU on the same
machine, so the two columns differ only in the device:

  image    the Docker image's path (OCR, tables, layout, structure recovery):
           CPU at 1 thread (as shipped), CPU at 4 threads (the box's best), GPU
           (fp32, `gpu_device.py`), and GPU again (is it stable run to run?).
  docling  docling-serve, run locally and reached over HTTP like the Space:
           CPU (CUDA hidden) then GPU.
  glm-ocr  glm-ocr/service.py + model_server.py over HTTP: GPU fp16 on every
           page, CPU on three pages for a same-machine reference; then,
           optionally, vLLM serving the same model (the route the glm-ocr
           Dockerfile names for GPU hardware).

Steps are logged to outputs/kernel-log.jsonl; a failing step never hides the
others. GPU memory is sampled every second (outputs/gpu-mem.csv) with each leg's
start and end in the log, so a service's VRAM can be read off. Quality is scored
back in the repo with score.py. No paid API is called.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WORK = Path("/kaggle/working")
REPO = WORK / "wiseau"
HARNESS = REPO / "benchmarks" / "bakeoff" / "kaggle"
OUT = WORK / "outputs"
LOG = OUT / "kernel-log.jsonl"

BACKEND_PINS = ["transformers==5.22.1", "timm==1.0.31", "opencv-python-headless==5.0.0.93", "accelerate==1.15.0"]
GLM_PINS = ["glmocr[selfhosted,server]==0.1.5", "pypdfium2==5.14.0", "flask==3.1.3", "waitress==3.0.2"]
DOCLING_SERVE = "docling-serve[easyocr]==1.27.0"   # the tag the docling Space pins (docling/Dockerfile); EasyOCR as there
VLLM = "vllm==0.22.1"
# Whole documents: two image-only journals (36 and 30 pages) for the image path; one for the slow engines.
DOCS_IMAGE = "pdf-test/Volume 4 Number 1.pdf,pdf-test/Volume 5 Number 1.pdf"
DOCS_SLOW = "pdf-test/Volume 4 Number 1.pdf"
CPU_ONLY = {"CUDA_VISIBLE_DEVICES": ""}
IMAGE_ENV = {"WISEAU_TABLES": "tatr", "WISEAU_LAYOUT": "ppdl", "WISEAU_STRUCTURE": "all"}
GLM_SAMPLE = "V2_p9,V4_p27,V5N1_p18"        # CPU reference pages: prose, single column, table
# Kaggle stops a session at 12 h, and a stopped session may keep no output. Legs
# are cut to fit this budget, and one with under 10 minutes left is skipped.
STARTED = time.time()
BUDGET = 10.5 * 3600


def log(step: str, **kw) -> None:
    rec = {"step": step, "t": round(time.time(), 1), **kw}
    print(json.dumps(rec)[:3000], flush=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def sh(step: str, cmd, env=None, timeout=None, cwd=None) -> int:
    started = time.time()
    try:
        done = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
                              env={**os.environ, **(env or {})}, timeout=timeout, cwd=cwd)
        log(step, cmd=cmd if isinstance(cmd, str) else " ".join(cmd), rc=done.returncode,
            seconds=round(time.time() - started, 1), stdout=done.stdout[-8000:], stderr=done.stderr[-8000:])
        return done.returncode
    except subprocess.TimeoutExpired as exc:
        log(step, cmd=str(cmd), rc=None, error="timeout", seconds=round(time.time() - started, 1),
            stdout=(exc.stdout or b"")[-8000:].decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout)[-8000:])
        return -1


def remaining() -> float:
    return BUDGET - (time.time() - STARTED)


def leg(name: str, env: dict, *args: str, timeout: int = 4 * 3600) -> int:
    if remaining() < 600:
        log("leg-skipped", leg=name, reason="time budget")
        return -2
    log("leg-start", leg=name)
    rc = sh(f"leg:{name}", [sys.executable, str(HARNESS / "e2e.py"), name, "--out", str(OUT / name), *args],
            env=env, timeout=int(min(timeout, remaining())))
    log("leg-end", leg=name, rc=rc)
    return rc


def start(step: str, cmd: list[str], env: dict, log_file: Path) -> subprocess.Popen:
    handle = log_file.open("w")
    proc = subprocess.Popen(cmd, env={**os.environ, **env}, stdout=handle, stderr=subprocess.STDOUT)
    log(step, cmd=" ".join(cmd), pid=proc.pid)
    return proc


def wait_health(step: str, url: str, proc: subprocess.Popen, limit: int = 1800) -> bool:
    started = time.time()
    while time.time() - started < limit:
        if proc.poll() is not None:
            log(step, ok=False, error=f"exited with {proc.returncode}")
            return False
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    log(step, ok=True, seconds=round(time.time() - started, 1))
                    return True
        except Exception:
            pass
        time.sleep(3)
    log(step, ok=False, error="not healthy in time")
    return False


def stop(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    time.sleep(5)   # let the GPU memory go before the next leg


def setup() -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    # Kaggle unpacks an uploaded tar into the dataset; accept the tar or the tree.
    tars = sorted(Path("/kaggle/input").rglob("e2e.tar"))
    trees = sorted(p.parents[3] for p in Path("/kaggle/input").rglob("wiseau/benchmarks/bakeoff/kaggle/e2e.py"))
    if tars:
        shutil.unpack_archive(str(tars[0]), str(WORK), "tar")
    elif trees:
        shutil.copytree(trees[0], REPO, dirs_exist_ok=True)   # /kaggle/input is read-only
    else:
        log("setup", error="neither e2e.tar nor an extracted wiseau/ tree under /kaggle/input")
        return False
    log("setup", ok=True)
    return True


def make_venv(step: str, path: Path, system_site: bool) -> int:
    """A virtualenv with pip in it. Kaggle's Python has no working `ensurepip`, so
    the stdlib `venv` fails there (run v2); `virtualenv` brings its own pip."""
    if sh(f"{step}:virtualenv", [sys.executable, "-m", "pip", "install", "-q", "virtualenv"]) != 0:
        return 1
    return sh(step, [sys.executable, "-m", "virtualenv", "-q", *(["--system-site-packages"] if system_site else []),
                     str(path)])


def wanted(group: str) -> bool:
    """Leg groups to run: all, or those listed in the dataset's LEGS file (push_e2e.sh LEGS=...)."""
    chosen = REPO / "LEGS"
    return not chosen.exists() or group in chosen.read_text().replace(",", " ").split()


def docling_setup() -> Path | None:
    """docling-serve in its own virtualenv (its pins never touch the backend's)."""
    venv = WORK / "docling-venv"
    if make_venv("docling:venv", venv, system_site=True) != 0 or \
            sh("docling:install", [str(venv / "bin" / "python"), "-m", "pip", "install", "-q", DOCLING_SERVE],
               timeout=3600) != 0:
        return None
    sh("docling:versions", f"{venv / 'bin' / 'python'} -m pip list 2>/dev/null | grep -i -E "
       "'^(docling|docling-serve|docling-core|easyocr|torch|transformers) '")
    return venv


def docling_leg(venv: Path, name: str, device_env: dict) -> None:
    """docling-serve configured as the Space is (docling/Dockerfile), reached over HTTP."""
    serve_env = {"UVICORN_HOST": "127.0.0.1", "UVICORN_PORT": "5001", "UVICORN_WORKERS": "1",
                 "DOCLING_SERVE_ENG_LOC_NUM_WORKERS": "1", "DOCLING_SERVE_ENG_LOC_SHARE_MODELS": "true",
                 "DOCLING_SERVE_LOAD_MODELS_AT_BOOT": "true", "DOCLING_SERVE_OPTIONS_CACHE_SIZE": "1",
                 "DOCLING_SERVE_MAX_SYNC_WAIT": "7200", "DOCLING_SERVE_MAX_NUM_PAGES": "500",
                 "DOCLING_SERVE_MAX_FILE_SIZE": "104857600", "DOCLING_SERVE_ENABLE_UI": "false",
                 "DOCLING_SERVE_ENABLE_REMOTE_SERVICES": "false", "OMP_NUM_THREADS": "4",
                 "DOCLING_NUM_THREADS": "4"}
    client_env = {"WISEAU_DOCLING_BASE": "http://127.0.0.1:5001", "WISEAU_DOCLING_TIMEOUT": "7200", **CPU_ONLY}
    if remaining() < 600:
        log("leg-skipped", leg=name, reason="time budget")
        return
    proc = start(f"{name}:serve", [str(venv / "bin" / "docling-serve"), "run"],
                 {**serve_env, **device_env}, OUT / f"{name}-serve.log")
    if wait_health(f"{name}:health", "http://127.0.0.1:5001/health", proc):
        leg(name, client_env, "--engine", "docling", "--docs", DOCS_SLOW, timeout=6 * 3600)
    stop(proc)


def main() -> None:
    if not setup():
        return
    sh("env:nvidia-smi", "nvidia-smi")
    sh("env:cpu", "nproc; free -g; lscpu | grep -E 'Model name|^CPU\\(s\\)'")
    sh("env:python", [sys.executable, "--version"])
    sampler = subprocess.Popen("nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu --format=csv,noheader -l 1 "
                               f"> {OUT / 'gpu-mem.csv'}", shell=True)

    sh("install:tesseract", "apt-get update -qq && apt-get install -y -qq tesseract-ocr tesseract-ocr-eng", timeout=1800)
    sh("env:tesseract", "tesseract --version")
    sh("install:backend", [sys.executable, "-m", "pip", "install", "-q", "-r", str(REPO / "backend" / "requirements.txt"),
                           *BACKEND_PINS], timeout=3600)
    sh("env:versions", f"{sys.executable} -m pip list 2>/dev/null | grep -i -E "
       "'^(torch|torchvision|transformers|timm|pymupdf|pymupdf4llm|opencv-python-headless) '")

    # 1. The image's path, in process.
    if wanted("image"):
        image_legs()

    docling = docling_setup() if wanted("docling") else None
    if docling:
        docling_leg(docling, "docling-gpu", {"DOCLING_DEVICE": "cuda"})

    if wanted("glm") or wanted("vllm"):
        glm_legs()

    # 4. docling on CPU last: the longest leg, and the one the time budget may cut.
    if docling:
        docling_leg(docling, "docling-cpu", {**CPU_ONLY, "DOCLING_DEVICE": "cpu"})

    sampler.terminate()
    # Kaggle keeps all of /kaggle/working as the kernel's output: leave only the results.
    for scratch in ("docling-venv", "vllm-venv", "wiseau"):
        shutil.rmtree(WORK / scratch, ignore_errors=True)
    shutil.make_archive(str(WORK / "outputs"), "zip", OUT)
    log("done")


def image_legs() -> None:
    leg("image-cpu1", {**IMAGE_ENV, **CPU_ONLY, "WISEAU_TABLE_THREADS": "1", "WISEAU_LAYOUT_THREADS": "1"},
        "--docs", DOCS_IMAGE)
    leg("image-cpu4", {**IMAGE_ENV, **CPU_ONLY, "WISEAU_TABLE_THREADS": "4", "WISEAU_LAYOUT_THREADS": "4"},
        "--docs", DOCS_IMAGE)
    leg("image-gpu", {**IMAGE_ENV, "WISEAU_TABLE_THREADS": "1", "WISEAU_LAYOUT_THREADS": "1"},
        "--device", "cuda", "--docs", DOCS_IMAGE)
    leg("image-gpu-repeat", {**IMAGE_ENV, "WISEAU_TABLE_THREADS": "1", "WISEAU_LAYOUT_THREADS": "1"},
        "--device", "cuda")


def glm_legs() -> None:
    # 3. GLM-OCR: the service and its transformers model server, over HTTP.
    glm = REPO / "glm-ocr"
    client_env = {"WISEAU_GLM_OCR_BASE": "http://127.0.0.1:7860", "WISEAU_GLM_OCR_TIMEOUT": "7200", **CPU_ONLY}
    if sh("glm:install", [sys.executable, "-m", "pip", "install", "-q", *BACKEND_PINS, *GLM_PINS], timeout=3600) == 0:
        sh("glm:versions", f"{sys.executable} -m pip list 2>/dev/null | grep -i -E '^(glmocr|transformers|torch) '")
        runs = (("glm-ocr-gpu-fp16", {"GLM_OCR_DEVICE": "cuda", "GLM_OCR_DTYPE": "float16",
                                      "GLM_OCR_LAYOUT_DEVICE": "cuda"}, ["--docs", DOCS_SLOW]),
                ("glm-ocr-gpu-fp32", {"GLM_OCR_DEVICE": "cuda", "GLM_OCR_DTYPE": "float32",
                                      "GLM_OCR_LAYOUT_DEVICE": "cuda"}, ["--pages", GLM_SAMPLE]),
                ("glm-ocr-cpu", {**CPU_ONLY, "GLM_OCR_DEVICE": "cpu", "GLM_OCR_DTYPE": "float32",
                                 "GLM_OCR_THREADS": "4"}, ["--pages", GLM_SAMPLE]))
        for name, device_env, extra in (runs if wanted("glm") else ()):
            env = {"GLM_OCR_PORT": "8080", "PORT": "7860", "GLM_OCR_MAX_PAGES": "500", "GLM_OCR_MAX_FILE_MB": "100",
                   **device_env}
            model = start(f"{name}:model", [sys.executable, str(glm / "model_server.py")], env, OUT / f"{name}-model.log")
            service = None
            if wait_health(f"{name}:model-health", "http://127.0.0.1:8080/health", model):
                service = start(f"{name}:service", [sys.executable, str(glm / "service.py")], env,
                                OUT / f"{name}-service.log")
                if wait_health(f"{name}:service-health", "http://127.0.0.1:7860/health", service):
                    leg(name, client_env, "--engine", "glm-ocr", *extra, timeout=6 * 3600)
            stop(service)
            stop(model)

        # 3b. Optional: vLLM serving the model in place of model_server.py (own virtualenv).
        if wanted("vllm") and (os.environ.get("RUN_VLLM") == "1" or (REPO / "RUN_VLLM").exists()):
            vvenv = WORK / "vllm-venv"
            if make_venv("vllm:venv", vvenv, system_site=False) == 0 and \
                    sh("vllm:install", [str(vvenv / "bin" / "python"), "-m", "pip", "install", "-q", VLLM],
                       timeout=3600) == 0:
                model = start("glm-ocr-vllm:model", [str(vvenv / "bin" / "vllm"), "serve", "zai-org/GLM-OCR",
                                                     "--served-model-name", "glm-ocr", "--dtype", "half",
                                                     "--port", "8080", "--host", "127.0.0.1",
                                                     "--gpu-memory-utilization", "0.6", "--max-model-len", "16384"],
                              {}, OUT / "glm-ocr-vllm-model.log")
                service = None
                if wait_health("glm-ocr-vllm:model-health", "http://127.0.0.1:8080/health", model):
                    env = {"PORT": "7860", "GLM_OCR_PORT": "8080", "GLM_OCR_MAX_PAGES": "500",
                           "GLM_OCR_MAX_FILE_MB": "100", "GLM_OCR_LAYOUT_DEVICE": "cuda"}
                    service = start("glm-ocr-vllm:service", [sys.executable, str(glm / "service.py")], env,
                                    OUT / "glm-ocr-vllm-service.log")
                    if wait_health("glm-ocr-vllm:service-health", "http://127.0.0.1:7860/health", service):
                        leg("glm-ocr-vllm", client_env, "--engine", "glm-ocr", "--docs", DOCS_SLOW, timeout=4 * 3600)
                stop(service)
                stop(model)



if __name__ == "__main__":
    main()
