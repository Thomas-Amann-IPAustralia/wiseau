"""Run a layout model over a sample's pages and keep its raw regions (roadmap 14 item 3.7b).

A spike, not a production engine: nothing here is imported by `backend/`. Each
page is rendered from its PDF, given to one layout model, and the model's
regions are written *as the model gave them* to

  outputs/<engine>/<KEY>_p<N>.regions.json

with the raw text (for a generative model), the parsed regions (or the parse
error: malformed or truncated JSON is a failure, never repaired), the model
revision, prompt, settings, image size, seconds, peak memory and the package
versions. `layout_regions.py` turns these into IR elements filled with
Tesseract's own words and scores them; this script only runs models.

  python layout_models.py pp-doclayoutv3 --set b            # CPU detector baseline
  python layout_models.py ovisocr3-layout --set b           # generative, layout-only prompt
  python layout_models.py ovisocr3-layout --pages V4_p27 --device cuda --dtype fp16   # Kaggle T4

Options: --set a..t (regions.py), --pages KEY,KEY, --device cpu|cuda,
--dtype fp32|fp16|bf16, --threads N (CPU), --dpi N (render, default 200),
--max-new-tokens N (generative, default 4096), --out DIR (default outputs/).
Pages already done are skipped unless --force. Run one engine at a time,
never side by side (post-mortem 2026-11-01): timings are the point.

Models load from the Hugging Face cache at pinned revisions (REVISIONS). No
engine here calls a paid or external API.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import resource
import sys
import time
import traceback
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from regions import DOCS, SAMPLE, SAMPLE_B, SAMPLE_C, SAMPLE_D, SAMPLE_E, SAMPLE_F, SAMPLE_L, SAMPLE_S, SAMPLE_T  # noqa: E402

SETS = {"a": SAMPLE, "b": SAMPLE_B, "c": SAMPLE_C, "d": SAMPLE_D, "e": SAMPLE_E, "f": SAMPLE_F,
        "l": SAMPLE_L, "s": SAMPLE_S, "t": SAMPLE_T}

REVISIONS = {
    # The revision the GLM-OCR service pins (glm-ocr/Dockerfile).
    "PaddlePaddle/PP-DocLayoutV3_safetensors": "e4f489dc536556fc1be02b973ba06756e0d4a2ac",
    # Head of main on 2026-11-10.
    "ATH-MaaS/OvisOCR3": "17f255e2c8ae6d18d5d5edb0a474bd4ed39aef90",
}
# OvisOCR3's own task prompts (prompts/*.txt in the model repo), used verbatim.
OVIS_PROMPTS = {"ovisocr3-layout": "document_layout", "ovisocr3-structured": "document_structured"}
# The model card's image bounds (mm_processor_kwargs), in pixels.
OVIS_MIN_PIXELS, OVIS_MAX_PIXELS = 448 * 448, 2880 * 2880
PPDL_THRESHOLD = 0.5  # the processor's post-processing default


def _arg(name: str, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def _versions() -> dict:
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("torch", "transformers", "accelerate", "pymupdf", "pillow", "vllm"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    return out


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def render(key: str, page_no: int, dpi: int):
    """The page as an RGB PIL image at ``dpi`` (the PDF's own page, no cleanup)."""
    import pymupdf
    from PIL import Image

    with pymupdf.open(ROOT / DOCS[key]) as doc:
        pix = doc[page_no - 1].get_pixmap(dpi=dpi, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# --- engines -------------------------------------------------------------------
# Each returns (setup dict, run(image) -> dict with regions/raw/...).


def _torch_setup(device: str, dtype: str, threads: int | None):
    import torch

    if threads:
        torch.set_num_threads(threads)
    return torch, {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}[dtype]


def ppdl_engine(device: str, dtype: str, threads: int | None):
    """PP-DocLayoutV3: a detector (no generation), boxes in its predicted reading order."""
    torch, tdtype = _torch_setup(device, dtype, threads)
    from transformers import AutoImageProcessor, AutoModelForObjectDetection

    repo = "PaddlePaddle/PP-DocLayoutV3_safetensors"
    t0 = time.perf_counter()
    model = AutoModelForObjectDetection.from_pretrained(repo, revision=REVISIONS[repo], dtype=tdtype).to(device).eval()
    proc = AutoImageProcessor.from_pretrained(repo, revision=REVISIONS[repo])
    load_s = time.perf_counter() - t0

    def run(image) -> dict:
        w, h = image.size
        with torch.inference_mode():
            inputs = proc(images=image, return_tensors="pt").to(device)
            inputs["pixel_values"] = inputs["pixel_values"].to(tdtype)
            outputs = model(**inputs)
            result = proc.post_process_object_detection(outputs, threshold=PPDL_THRESHOLD,
                                                        target_sizes=[(h, w)])[0]
        regions = []
        for score, label, box in zip(result["scores"], result["labels"], result["boxes"]):
            x0, y0, x1, y1 = (float(v) for v in box.tolist())
            regions.append({"bbox": [round(x0 * 1000 / w, 1), round(y0 * 1000 / h, 1),
                                     round(x1 * 1000 / w, 1), round(y1 * 1000 / h, 1)],
                            "category": model.config.id2label[int(label)], "score": round(float(score), 4)})
        return {"regions": regions}

    return {"model": repo, "revision": REVISIONS[repo], "threshold": PPDL_THRESHOLD,
            "load_seconds": round(load_s, 2)}, run


def ovis_engine(engine: str, device: str, dtype: str, threads: int | None, max_new: int):
    """OvisOCR3 through Transformers (not the card's vLLM route), greedy, one page a request."""
    torch, tdtype = _torch_setup(device, dtype, threads)
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForImageTextToText, AutoProcessor

    repo = "ATH-MaaS/OvisOCR3"
    path = Path(snapshot_download(repo, revision=REVISIONS[repo]))
    prompt_name = OVIS_PROMPTS[engine]
    prompt = (path / "prompts" / f"{prompt_name}.txt").read_text(encoding="utf-8")
    t0 = time.perf_counter()
    model = AutoModelForImageTextToText.from_pretrained(path, dtype=tdtype).to(device).eval()
    proc = AutoProcessor.from_pretrained(path)
    load_s = time.perf_counter() - t0
    messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
    text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    eos = set(model.generation_config.eos_token_id if isinstance(model.generation_config.eos_token_id, list)
              else [model.generation_config.eos_token_id])

    def run(image) -> dict:
        inputs = proc(text=[text], images=[image], return_tensors="pt",
                      size={"shortest_edge": OVIS_MIN_PIXELS, "longest_edge": OVIS_MAX_PIXELS}).to(device)
        if "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].to(tdtype)
        n_in = int(inputs["input_ids"].shape[1])
        grid = inputs.get("image_grid_thw")
        t = time.perf_counter()
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
        gen_s = time.perf_counter() - t
        new = out[0, n_in:].tolist()
        finish = "stop" if new and new[-1] in eos else "length"
        raw = proc.decode(new, skip_special_tokens=True)
        rec = {"raw": raw, "finish_reason": finish, "input_tokens": n_in, "new_tokens": len(new),
               "image_grid_thw": grid.tolist() if grid is not None else None,
               "generate_seconds": round(gen_s, 2)}
        rec.update(parse_regions(raw, finish))
        return rec

    return {"model": repo, "revision": REVISIONS[repo], "prompt": prompt_name,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "max_new_tokens": max_new, "do_sample": False, "min_pixels": OVIS_MIN_PIXELS,
            "max_pixels": OVIS_MAX_PIXELS, "route": "transformers", "load_seconds": round(load_s, 2)}, run


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n```\s*$", re.S)


def parse_regions(raw: str, finish: str) -> dict:
    """A generative model's layout JSON → regions, or a parse error. Only a code
    fence around the array is removed; nothing is repaired, and an output that hit
    the token limit is a failure even when what it holds would parse."""
    if finish != "stop":
        return {"regions": None, "parse_error": "token limit reached"}
    body = raw.strip()
    m = _FENCE.match(body)
    if m:
        body = m.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return {"regions": None, "parse_error": f"JSONDecodeError: {exc}"}
    if not isinstance(data, list):
        return {"regions": None, "parse_error": "not a JSON array"}
    regions, bad = [], 0
    for item in data:
        box = item.get("bbox") if isinstance(item, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            bad += 1
            continue
        region = {"bbox": [float(v) for v in box], "category": str(item.get("category", ""))}
        if "content" in item:
            region["content"] = item["content"]  # kept for inspection; never used as text
        regions.append(region)
    out = {"regions": regions}
    if bad:
        out["malformed_items"] = bad
    return out


# --- main ----------------------------------------------------------------------


def main() -> None:
    engine = sys.argv[1]
    device = _arg("--device", "cpu")
    dtype = _arg("--dtype", "fp32")
    threads = int(_arg("--threads")) if _arg("--threads") else None
    dpi = int(_arg("--dpi", "200"))
    max_new = int(_arg("--max-new-tokens", "4096"))
    out_root = Path(_arg("--out", str(HERE / "outputs")))
    sample = SETS[_arg("--set", "a").lower()]
    pages = [(k, n) for k, ns in sample.items() for n in ns]
    if _arg("--pages"):
        want = set(_arg("--pages").split(","))
        pages = [(k, n) for k, n in pages if f"{k}_p{n}" in want]
    if engine == "pp-doclayoutv3":
        setup, run = ppdl_engine(device, dtype, threads)
    elif engine in OVIS_PROMPTS:
        setup, run = ovis_engine(engine, device, dtype, threads, max_new)
    else:
        raise SystemExit(f"unknown engine {engine!r}")
    out_dir = out_root / engine
    out_dir.mkdir(parents=True, exist_ok=True)
    common = {**setup, "device": device, "dtype": dtype, "threads": threads, "dpi": dpi, "versions": _versions()}
    if device == "cuda":
        import torch

        common["gpu"] = torch.cuda.get_device_name(0)
    failed = 0
    for key, n in pages:
        path = out_dir / f"{key}_p{n}.regions.json"
        if path.exists() and "--force" not in sys.argv:
            continue
        image = render(key, n, dpi)
        if device == "cuda":
            import torch

            torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        try:
            rec = run(image)
        except Exception as exc:  # recorded, never hidden: a failed page is a result
            rec = {"regions": None, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        rec["seconds"] = round(time.perf_counter() - t0, 2)
        rec["peak_rss_mb"] = round(_peak_rss_mb(), 1)
        if device == "cuda":
            import torch

            rec["peak_vram_mb"] = round(torch.cuda.max_memory_allocated() / 2**20, 1)
        rec["image_px"] = list(image.size)
        rec["page"] = f"{key}_p{n}"
        failed += rec.get("regions") is None
        path.write_text(json.dumps({**common, **rec}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{key}_p{n}: {rec['seconds']} s, "
              f"{len(rec['regions']) if rec.get('regions') is not None else 'FAILED'} regions"
              + (f", {rec.get('new_tokens')} new tokens ({rec.get('finish_reason')})" if "raw" in rec else ""),
              flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
