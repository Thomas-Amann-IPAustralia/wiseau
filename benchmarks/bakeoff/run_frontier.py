"""Ask two frontier models to transcribe the five hardest hand-annotated pages.

User request (2026-11-09): pick five of the most difficult pages Claude has
transcribed by hand, have the flagship Gemini and OpenAI models transcribe them
"by hand" too, and put the page image beside all three attempts, so the user can
judge which model reads these documents best. This calls **paid** APIs; the user
asked for it in the same message.

Selection rule (fixed before any call): of the 72 hand-transcribed scan references
(set A's born-digital V16 pages are excluded: those references come from the text
layer, not a person or model reading the page), the five with the highest *median*
wer across every engine scored on them in `results.json` (oracle, consensus and
hidden-layer rows left out). That median is how hard the page is for the whole
toolbox, not for one engine.

Integrity: every response body is written to `raw/` byte for byte as it arrived,
before anything parses it, with its SHA-256 in `manifest.json`. The `.md` file is
the response's text parts joined exactly as returned (no strip, no clean-up). The
image sent is the PNG in `images/`, also hashed; the comparison page embeds that same
file. One successful request per page per model. A request the API rejects is kept
in `manifest.json` under `failed` (with its raw body) and may be sent again; a
successful answer is never re-requested, so no answer is picked from several.

  python benchmarks/bakeoff/run_frontier.py --pages V5N1_p18 --models gpt-6-astra
  python benchmarks/bakeoff/run_frontier.py            # every page, both models
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from regions import DOCS  # noqa: E402

OUT = HERE / "outputs" / "frontier-check"
PAGES = ["V5N1_p18", "V4_p19", "V2_p12", "V4N1_p7", "P63a_p7"]
DPI = 300
BUDGET_USD = 12.0
MAX_OUTPUT_TOKENS = 32768
# USD per 1M tokens (input, output), from the providers' model pages, 2026-11-09.
# Gemini 4 Argon is at its introductory price until 2026-11-30.
PRICES = {"gpt-6-astra": (10.0, 50.0), "gemini-4-argon": (2.0, 10.0)}

# One prompt for both models. It states the conventions Claude's references were
# typed under (regions.py, sets C-F), so all three attempts aim at the same target.
PROMPT = """\
Transcribe this scanned document page by hand, as a careful human transcriber would.

Rules:
- Reproduce every piece of printed or typed text exactly as it appears: same words, \
spelling, capitalisation, numbers, units and punctuation (keep curly quotes, en and \
em dashes as printed). Do not correct, normalise, summarise, translate or add anything, \
even where the original looks wrong.
- Keep the original line breaks: one printed line per output line, including words \
hyphenated at a line end.
- Follow natural reading order: finish a column before starting the next one.
- Read tables row by row, left to right across the whole row, with each cell's text \
in its original order. You may use Markdown tables.
- Leave out text that is part of a photograph or drawing, and handwritten marks.
- Output only the transcription.
"""


def render(key: str) -> bytes:
    doc_key, page = key.rsplit("_p", 1)
    with pymupdf.open(ROOT / DOCS[doc_key]) as doc:
        return doc[int(page) - 1].get_pixmap(dpi=DPI).tobytes("png")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def post(url: str, body: dict, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# The session's egress proxy drops a request that sends nothing back for 30 s, and
# a high-effort transcription thinks for longer than that. So OpenAI runs in
# background mode (submit, then poll the stored response) and Gemini streams.
def call_openai(model: str, png: bytes) -> tuple[int, bytes]:
    body = {
        "model": model,
        "background": True,
        "store": True,
        "reasoning": {"effort": "high"},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": PROMPT},
            {"type": "input_image", "detail": "original",
             "image_url": "data:image/png;base64," + base64.b64encode(png).decode()},
        ]}],
    }
    # The agent proxy injects the real key; the header just has to be present.
    auth = {"Authorization": "Bearer proxy"}
    status, raw = post("https://api.openai.com/v1/responses", body, auth)
    if status != 200:
        return status, raw
    rid = json.loads(raw)["id"]
    while True:
        time.sleep(10)
        status, raw = get(f"https://api.openai.com/v1/responses/{rid}", auth)
        if status != 200 or json.loads(raw).get("status") not in ("queued", "in_progress"):
            return status, raw


def call_gemini(model: str, png: bytes) -> tuple[int, bytes]:
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()},
             # Gemini's highest image setting (2240 tokens a page). OpenAI's is
             # detail "original"; each model reads the page at its best.
             "media_resolution": {"level": "MEDIA_RESOLUTION_ULTRA_HIGH"}},
        ]}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS,
                             "thinkingConfig": {"thinkingLevel": "high", "includeThoughts": True}},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           ":streamGenerateContent?alt=sse")
    return post(url, body, {"x-goog-api-key": "proxy"})


def sse_chunks(raw: bytes) -> list[dict]:
    """The JSON chunks of a Gemini server-sent-event stream, in order."""
    return [json.loads(line[5:]) for line in raw.decode("utf-8").splitlines()
            if line.startswith("data:")]


def text_and_usage(model: str, resp: dict) -> tuple[str, int, int, str]:
    """The answer text exactly as returned, billed tokens in/out, finish reason."""
    if model.startswith("gpt"):
        parts = [c["text"] for item in resp.get("output", []) if item.get("type") == "message"
                 for c in item.get("content", []) if c.get("type") == "output_text"]
        u = resp.get("usage", {})
        return "".join(parts), u.get("input_tokens", 0), u.get("output_tokens", 0), \
            resp.get("status", "?") + ("" if not resp.get("incomplete_details")
                                       else f" ({resp['incomplete_details']})")
    # Gemini: `resp` is the list of stream chunks. Text parts are joined in the
    # order they arrived; thought summaries are left out; usage is the last chunk's.
    parts, finish = [], "?"
    for chunk in resp:
        cand = (chunk.get("candidates") or [{}])[0]
        parts += [p["text"] for p in cand.get("content", {}).get("parts", [])
                  if "text" in p and not p.get("thought")]
        finish = cand.get("finishReason", finish)
    u = resp[-1].get("usageMetadata", {}) if resp else {}
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return "".join(parts), u.get("promptTokenCount", 0), out, finish


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default=",".join(PAGES))
    ap.add_argument("--models", default="gpt-6-astra,gemini-4-argon")
    args = ap.parse_args()

    (OUT / "images").mkdir(parents=True, exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)
    mpath = OUT / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {
        "prompt": PROMPT, "dpi": DPI, "pages": PAGES, "images": {}, "calls": {}}
    spent = sum(c.get("usd", 0) for c in [*manifest["calls"].values(), *manifest.get("failed", [])])

    for key in args.pages.split(","):
        png = render(key)
        img = OUT / "images" / f"{key}.png"
        if img.exists() and img.read_bytes() != png:
            sys.exit(f"{img} differs from a fresh render; refusing to send a different image")
        img.write_bytes(png)
        manifest["images"][key] = sha(png)
        for model in args.models.split(","):
            cid = f"{model}/{key}"
            if cid in manifest["calls"]:
                print(f"{cid}: already done, skipping")
                continue
            pin, pout = PRICES[model]
            worst = (20000 * pin + MAX_OUTPUT_TOKENS * pout) / 1e6
            if spent + worst > BUDGET_USD:
                sys.exit(f"budget: {spent:.2f} spent + {worst:.2f} worst case > {BUDGET_USD}")
            t0 = time.time()
            status, raw = (call_openai if model.startswith("gpt") else call_gemini)(model, png)
            secs = round(time.time() - t0, 1)
            rpath = OUT / "raw" / f"{model}__{key}.{'json' if model.startswith('gpt') else 'sse'}"
            rpath.write_bytes(raw)  # verbatim, before any parsing
            entry = {"http": status, "seconds": secs, "raw": rpath.name, "raw_sha256": sha(raw),
                     "image_sha256": sha(png), "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if status == 200:
                resp = json.loads(raw) if model.startswith("gpt") else sse_chunks(raw)
                text, tin, tout, finish = text_and_usage(model, resp)
                md = OUT / model / f"{key}.md"
                md.parent.mkdir(exist_ok=True)
                md.write_bytes(text.encode("utf-8"))
                usd = (tin * pin + tout * pout) / 1e6
                entry.update(text=f"{model}/{key}.md", text_sha256=sha(text.encode("utf-8")),
                             tokens_in=tin, tokens_out=tout, finish=finish, usd=round(usd, 4))
            else:
                # Not billed if rejected, but book the worst case to be safe.
                entry.update(error=raw[:500].decode("utf-8", "replace"), usd=round(worst, 4))
            spent += entry["usd"]
            if status == 200:
                manifest["calls"][cid] = entry
            else:  # kept on record; a re-run sends the request again
                manifest.setdefault("failed", []).append({"call": cid, **entry})
                failed_name = f"FAILED-{len(manifest['failed'])}__{rpath.name}"
                rpath.rename(OUT / "raw" / failed_name)
                manifest["failed"][-1]["raw"] = failed_name
            mpath.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
            print(f"{cid}: http {status}, {secs}s, ${entry['usd']:.3f} (total ${spent:.2f})"
                  + (f", {entry.get('finish')}" if status == 200 else f": {entry['error'][:200]}"))


if __name__ == "__main__":
    main()
