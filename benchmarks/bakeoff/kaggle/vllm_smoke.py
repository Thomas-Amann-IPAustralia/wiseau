"""OvisOCR3 through the model card's vLLM route, one page (Kaggle leg, optional).

Run by `kernel.py` inside its own virtualenv with vllm==0.22.1, so vLLM's
pinned torch never mixes with Kaggle's. Writes <out>/V2_p9.regions.json in the
same shape as `layout_models.py`, or the exception if vLLM refuses the T4
(compute capability 7.5) or the linear-attention (gated delta net) kernels.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path("/kaggle/working/wiseau/benchmarks/bakeoff")
sys.path.insert(0, str(HERE))
from layout_models import OVIS_MAX_PIXELS, OVIS_MIN_PIXELS, REVISIONS, _versions, parse_regions, render  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rec: dict = {"route": "vllm", "versions": _versions(), "page": "V2_p9", "dpi": 200}
    try:
        from huggingface_hub import snapshot_download
        from vllm import LLM, SamplingParams

        path = Path(snapshot_download("ATH-MaaS/OvisOCR3", revision=REVISIONS["ATH-MaaS/OvisOCR3"]))
        t0 = time.perf_counter()
        llm = LLM(model=str(path), tensor_parallel_size=1, gpu_memory_utilization=0.8, dtype="float16",
                  gdn_prefill_backend="triton", limit_mm_per_prompt={"image": 1})
        rec["load_seconds"] = round(time.perf_counter() - t0, 2)
        tok = llm.get_tokenizer()
        prompt_text = (path / "prompts" / "document_layout.txt").read_text(encoding="utf-8")
        prompt = tok.apply_chat_template([{"role": "user", "content": [{"type": "image"},
                                                                       {"type": "text", "text": prompt_text}]}],
                                         tokenize=False, add_generation_prompt=True, enable_thinking=False)
        image = render("V2", 9, 200)
        t0 = time.perf_counter()
        res = llm.generate([{"prompt": prompt, "multi_modal_data": {"image": [image]},
                             "mm_processor_kwargs": {"images_kwargs": {"min_pixels": OVIS_MIN_PIXELS,
                                                                       "max_pixels": OVIS_MAX_PIXELS}}}],
                           SamplingParams(max_tokens=4096, temperature=0.0))[0].outputs[0]
        rec["seconds"] = round(time.perf_counter() - t0, 2)
        rec.update({"raw": res.text, "finish_reason": res.finish_reason, "new_tokens": len(res.token_ids)})
        rec.update(parse_regions(res.text, res.finish_reason))
    except Exception as exc:
        rec.update({"regions": None, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
    (out / "V2_p9.regions.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
