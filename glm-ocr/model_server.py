"""Minimal OpenAI-compatible chat-completions server for GLM-OCR on CPU (ADR-034).

The GLM-OCR SDK (`glmocr`) owns the *pipeline*: it rasterizes the PDF, runs the
PP-DocLayoutV3 layout model, crops each detected region, and asks a model server
to transcribe each crop with a task prompt ("Text Recognition:", "Table
Recognition:", ...). It speaks the OpenAI ``/v1/chat/completions`` protocol to
that server and expects vLLM or SGLang behind it.

Neither runs on a free CPU Space, so this file is the model server instead: it
loads ``zai-org/GLM-OCR`` with Hugging Face ``transformers`` and answers the
subset of the protocol the SDK sends (one user message holding one image and
one text prompt; greedy decoding). It is deliberately small:

* **One generation at a time.** Inference is serialized behind a lock — on two
  vCPUs, parallel ``generate`` calls only thrash the cache and multiply memory;
  the SDK's worker pool simply queues.
* **Greedy decoding** (``do_sample=False``) whatever ``temperature`` says. The
  SDK already asks for temperature 0 / top-k 1; honouring it as greedy keeps a
  given region's transcription reproducible on a fixed build (ADR-013 accepts
  run-to-run variation from this engine, but there is no reason to add it).

Run: ``python model_server.py`` (env: ``GLM_OCR_MODEL``, ``GLM_OCR_PORT``,
``GLM_OCR_THREADS``, ``GLM_OCR_DTYPE``, ``GLM_OCR_DEVICE``).

``GLM_OCR_DEVICE`` (default ``cpu``) can name a CUDA device, and
``GLM_OCR_DTYPE=float16`` is accepted for GPUs without native bfloat16 (a T4).
Both exist for the GPU benchmark (``benchmarks/bakeoff/kaggle/e2e_kernel.py``);
the Space stays on CPU.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
import uuid

import torch
from flask import Flask, jsonify, request
from PIL import Image
from transformers import AutoProcessor, GlmOcrForConditionalGeneration

logger = logging.getLogger("glm_ocr_model_server")

MODEL_ID = os.environ.get("GLM_OCR_MODEL", "zai-org/GLM-OCR")
PORT = int(os.environ.get("GLM_OCR_PORT", "8080"))
THREADS = int(os.environ.get("GLM_OCR_THREADS", str(os.cpu_count() or 2)))
# bfloat16 halves memory and is usually faster on CPUs with AVX512-BF16/AMX;
# float32 is the safe default everywhere else.
DTYPE = {"bfloat16": torch.bfloat16, "float16": torch.float16,
         "float32": torch.float32}[os.environ.get("GLM_OCR_DTYPE", "float32")]
DEVICE = os.environ.get("GLM_OCR_DEVICE", "cpu").strip() or "cpu"
MAX_NEW_TOKENS_CAP = int(os.environ.get("GLM_OCR_MAX_NEW_TOKENS", "4096"))

torch.set_num_threads(THREADS)

processor = AutoProcessor.from_pretrained(MODEL_ID)
model = GlmOcrForConditionalGeneration.from_pretrained(MODEL_ID, dtype=DTYPE).to(DEVICE)
model.eval()
_lock = threading.Lock()

app = Flask(__name__)


def _image_from_url(url: str) -> Image.Image:
    """Decode a ``data:`` URI (the only form the SDK sends) into an RGB image."""
    if not url.startswith("data:"):
        raise ValueError("only data: image URLs are accepted")
    payload = url.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")


def _to_hf_messages(messages: list[dict]) -> list[dict]:
    """Translate OpenAI-style content parts into the processor's chat format."""
    converted = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            converted.append({"role": message.get("role", "user"), "content": [{"type": "text", "text": content}]})
            continue
        parts = []
        for part in content or []:
            if part.get("type") == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
                parts.append({"type": "image", "image": _image_from_url(url)})
            elif part.get("type") == "text":
                parts.append({"type": "text", "text": part.get("text", "")})
        converted.append({"role": message.get("role", "user"), "content": parts})
    return converted


@app.post("/v1/chat/completions")
def chat_completions():
    body = request.get_json(force=True)
    try:
        messages = _to_hf_messages(body.get("messages", []))
    except Exception as exc:  # noqa: BLE001 - malformed input is the caller's error
        return jsonify({"error": {"message": f"bad request: {exc}"}}), 400
    max_new = min(int(body.get("max_tokens") or MAX_NEW_TOKENS_CAP), MAX_NEW_TOKENS_CAP)
    repetition_penalty = float(body.get("repetition_penalty") or 1.0)

    with _lock:
        started = time.perf_counter()
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
        )
        inputs.pop("token_type_ids", None)
        inputs = inputs.to(DEVICE)
        with torch.inference_mode():
            output = model.generate(
                **inputs, max_new_tokens=max_new, do_sample=False, repetition_penalty=repetition_penalty
            )
        new_tokens = output[0][inputs["input_ids"].shape[1]:]
        text = processor.decode(new_tokens, skip_special_tokens=True)
        elapsed = time.perf_counter() - started
    # Say when a region was cut off at the cap rather than finished: a truncated
    # region is how a runaway shows up, and "stop" hid it from the SDK and the logs.
    eos_ids = model.generation_config.eos_token_id
    eos_ids = set(eos_ids if isinstance(eos_ids, list) else [eos_ids])
    truncated = len(new_tokens) >= max_new and int(new_tokens[-1]) not in eos_ids
    finish_reason = "length" if truncated else "stop"
    log = logger.warning if truncated else logger.info
    log("generated %d tokens in %.1fs (finish_reason=%s)", len(new_tokens), elapsed, finish_reason)

    return jsonify(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "glm-ocr"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": int(inputs["input_ids"].shape[1]),
                "completion_tokens": int(len(new_tokens)),
                "total_tokens": int(inputs["input_ids"].shape[1] + len(new_tokens)),
            },
        }
    )


@app.get("/v1/models")
def models():
    return jsonify({"object": "list", "data": [{"id": "glm-ocr", "object": "model"}]})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app.run(host="127.0.0.1", port=PORT, threaded=True)
