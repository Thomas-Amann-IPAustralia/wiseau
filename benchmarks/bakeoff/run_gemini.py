"""Run Gemini over the bake-off sample, under a hard spending cap.

This is the one script in the harness that calls a **paid** API. It needs the
user's explicit go-ahead and a budget (post-mortem 2026-11-01).

How the cap works (client-side; nothing here changes the Google billing account):

- A ledger file (`outputs/<model>/spend.json`) records the actual cost of every request
  ever sent with this script. It persists across runs, so the smoke test counts too.
- Before each request, the input is counted with `countTokens`, which is free. The
  request is then priced at its *worst case*: those input tokens plus the full
  `maxOutputTokens` allowance (thinking is billed as output and counts against that
  allowance). If ledger + worst case would exceed `--budget`, the request is not sent
  and the script stops.
- After each response, the ledger is updated from `usageMetadata`, the billed counts.
- No retries. Any error stops the run, and a failed request is booked at its worst
  case (it may have been billed). Re-running resumes with the pages not yet done.

  python benchmarks/bakeoff/run_gemini.py --smoke             # 1 page: V4_p26
  python benchmarks/bakeoff/run_gemini.py                     # all 10 pages

The pages are sent as 200-dpi PNGs, the resolution GLM-OCR renders at, so every
engine reads the same pixels. The page's text layer is not sent.
"""
from __future__ import annotations

import argparse
import base64
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
from regions import DOCS, SAMPLE  # noqa: E402

API = "https://generativelanguage.googleapis.com/v1beta/models"
# USD per 1M tokens. Google's pricing page does not list gemini-3.5-flash (checked
# 2026-11-03); these are the rates third-party price trackers consistently report.
PRICES = {"gemini-3.5-flash": (1.50, 9.00)}
MAX_OUTPUT_TOKENS = 8192
PROMPT = (
    "Transcribe this document page into Markdown. Reproduce every piece of text "
    "exactly as printed, in natural reading order (finish a column before starting "
    "the next). Render tables as Markdown tables, keeping each value in its own "
    "cell. Do not summarise, correct, translate or add anything. Skip text that "
    "is part of a photograph. Output only the Markdown."
)


def post(url: str, body: dict) -> dict:
    # The agent proxy injects the real key; the header just has to be present.
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": "proxy"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def page_png(doc_key: str, n: int) -> bytes:
    doc = pymupdf.open(ROOT / DOCS[doc_key])
    return doc[n - 1].get_pixmap(dpi=200).tobytes("png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--budget", type=float, default=1.00, help="hard cap in USD, across all runs")
    ap.add_argument("--smoke", action="store_true", help="send one page only (V4_p26)")
    ap.add_argument("--max-output", type=int, default=MAX_OUTPUT_TOKENS, help="output+thinking token allowance")
    args = ap.parse_args()
    price_in, price_out = PRICES[args.model]

    out = HERE / "outputs" / args.model
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "spend.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"budget_usd": args.budget, "requests": []}
    spent = sum(r["cost_usd"] for r in ledger["requests"])
    timings_path = out / "timings.json"
    timings = json.loads(timings_path.read_text()) if timings_path.exists() else {}

    keys = [("V4", 26)] if args.smoke else [(d, n) for d, ns in SAMPLE.items() for n in ns]
    for doc_key, n in keys:
        key = f"{doc_key}_p{n}"
        if (out / f"{key}.md").exists() and not args.smoke:
            continue  # already converted (resume after a stop); delete the file to redo it
        contents = [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(page_png(doc_key, n)).decode()}},
            {"text": PROMPT},
        ]}]
        counted = post(f"{API}/{args.model}:countTokens", {"contents": contents})["totalTokens"]
        worst = (counted * price_in + args.max_output * price_out) / 1e6
        if spent + worst > args.budget:
            print(f"STOP before {key}: spent ${spent:.4f} + worst case ${worst:.4f} > budget ${args.budget:.2f}")
            break
        body = {"contents": contents, "generationConfig": {"maxOutputTokens": args.max_output, "temperature": 0}}
        t = time.perf_counter()
        try:
            resp = post(f"{API}/{args.model}:generateContent", body)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            # We cannot know whether a failed request was billed, so book its worst case.
            spent += worst
            ledger["requests"].append({"page": key, "error": str(exc)[:200], "cost_usd": round(worst, 6),
                                       "note": "failed; booked at worst case"})
            ledger["spent_usd"] = round(spent, 6)
            ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
            print(f"STOP at {key}: {exc} (booked worst case ${worst:.4f}; total ${spent:.4f})")  # no retries
            break
        secs = round(time.perf_counter() - t, 1)
        usage = resp.get("usageMetadata", {})
        tin = usage.get("promptTokenCount", counted)
        tout = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        cost = (tin * price_in + tout * price_out) / 1e6
        spent += cost
        ledger["requests"].append({"page": key, "input_tokens": tin, "output_tokens": tout,
                                   "thought_tokens": usage.get("thoughtsTokenCount", 0), "cost_usd": round(cost, 6)})
        ledger["spent_usd"] = round(spent, 6)
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
        cand = (resp.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        (out / f"{key}.md").write_text(text)
        timings[key] = secs
        timings_path.write_text(json.dumps(timings, indent=2) + "\n")
        print(f"{key}: {secs} s, in {tin} / out {tout} tok, finish={cand.get('finishReason')}, "
              f"${cost:.4f} (total ${spent:.4f} of ${args.budget:.2f})", flush=True)


if __name__ == "__main__":
    main()
