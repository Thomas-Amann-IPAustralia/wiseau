# GLM-OCR diagnosis — 2026-11-02

Follow-up to the 2026-11-01 bake-off. Its open question was whether GLM-OCR's
runaway generations and errors came from the model or from how we served it. This run
tested that, with **no paid APIs**. The only downloads were the pinned model weights
and the pip packages.

**Hardware:** 4 vCPU Xeon (AMX/bf16), 15 GB, no GPU, the same class of box as the
bake-off. Every run went **one at a time** (no contention).
**Software:** `glm-ocr/requirements.txt` exactly (glmocr 0.1.5, transformers 5.22.1,
torch 2.15.0 CPU) and the revision-pinned weights from the Dockerfile.

## Method

1. A stub OpenAI server recorded every request the SDK sends: the crop image, the
   prompt and the sampling parameters. The SDK was run against it on V16 p11, the
   page that ran away, and on the other nine pages of the bake-off sample. That gave
   **171 region requests**: the crops, prompts and sampling parameters the real
   pipeline sends.
2. A replay script fed each crop to the model in-process, the same way
   `model_server.py` does (same processor call, greedy `generate`). It varied one
   setting at a time and used a loop detector that stops a region once its last 200
   tokens repeat a period of 4 tokens or fewer.

## Finding 1: `repetition_penalty=1.1` causes the runaway

The SDK asks for `repetition_penalty: 1.1` on every region (`glmocr/config.yaml`), and
`model_server.py` honours it.

V16 p11, left index column (672×1792 crop, 1,548 prompt tokens, "Text Recognition:"):

| Setting | Result | Time |
|---|---|---|
| bf16, penalty 1.1 (as deployed) | Loops on `. . . .` after line 3 (`‘NE 02’`) until the 4,096-token cap | 304 s |
| **float32**, penalty 1.1 | Same loop (caught by the detector at 225 tokens) | — |
| bf16, **penalty 1.0** | Whole column, 683 tokens, no loop | 51 s |
| bf16, penalty 1.3 | No loop, but misspells repeated letters ("Zarina" for *Zanrina*) and keeps stray leader dots | 49 s |

- **The cause is not bf16.** float32 loops in exactly the same place. It is also 3×
  slower (4.2 vs 13.5 tok/s).
- **The model state is not leaking between requests.** The processor returns
  `mm_token_type_ids`, so M-RoPE positions are built correctly. The
  `pop("token_type_ids")` in `model_server.py` does nothing, because that key is never
  returned. `rope_deltas` is cached on the model but recomputed at every prefill.
- **"Half the page lost" is explained.** The SDK's `clean_repeated_content` removes the
  dots afterwards, so what survives is the three lines written before the loop began.

## Finding 2: penalty 1.0 is safe and more faithful on the whole sample

All 166 regions of the other nine sample pages were replayed at 1.1 and at 1.0:

| | penalty 1.1 | penalty 1.0 |
|---|---|---|
| Regions that looped or hit the cap | 0 | 0 |
| Regions with identical output | 134 | 134 |
| Total generation time | 962 s | 1,209 s* |

\* Most of the extra time is the V16 p90 table, where 1.0 transcribes about twice as
much (below). This is real work done, not slower decoding.

Where the two settings differ (32 regions), 1.1 consistently strips repeated tokens
that are on the page:

- **Sentence-final periods dropped:** "Japan" vs "Japan.", "QLD" vs "QLD.", and others.
- **Spaces dropped:** "No1," vs "No 1,", "Applicant:Dudley", "Sim'in", "No.:89/001".
- **Opening quotes dropped:** "Scania", "Redlands", "Keijourna" (with 1.0 they keep the
  `‘`).
- **Misspelling:** "colouuration" (1.1) vs "colouration" (1.0).
- **V16 p90 contact table:** 36 names transcribed at 1.1 vs 61 at 1.0.
- **V2 p17 typewriter table:** at 1.1, Mean and Range are fused into one cell
  ("3.8cm3.4-4.3cm"). At 1.0 they are separate cells.

The only change in the other direction is that 1.0 renders F₁ as plain "F1" instead of
`$\mathrm{F}_{1}$`, which is arguably better for this project's readers.

The bake-off also saw two runaways on scanned pages. They **did not reproduce at either
setting** on these nine pages, run serially. They may have come from pages outside the
sample, or from timing contention. The second is only a guess and was not verified.

**Fix applied:** `glm-ocr/service.py` now overrides the SDK's penalty and defaults it to
1.0 (`GLM_OCR_REPETITION_PENALTY`). End-to-end check with the real `model_server.py` and
`service.py` on V16 p11: the whole page converts in **2 min 2 s**, with both index
columns complete and no truncated region. In the bake-off, this page lost its left
column after a 5-minute runaway.

## Finding 3: truncation was invisible

`model_server.py` always returned `finish_reason: "stop"`, even when a region was cut off
at the cap, so the SDK and the logs could not tell a runaway from a finished region. It
now returns `"length"` and logs a warning when the cap is hit without an EOS token.

## Problems that remain (model or pipeline, not serving)

- **Layout merges side-by-side tables.** On V16 p90, PP-DocLayoutV3 sends the whole page
  (two independent three-column tables) as **one** table region of about 3,500 image
  tokens. Even at 1.0 the model writes left and right rows into one 8-column table:
  pairing is wrong, and the right half is shifted by a row ("Goulden, David |
  07 5460 1112 fax", which is George's fax). It also stops after 61 of about 100 names,
  without looping or hitting the cap. This is the same failure docling had on that page.
- **Page numbers shift up one line** in dotted-leader indexes: "Acacia pravissima 18",
  then "‘NE 02’" with no number. The text is complete but the alignment is wrong.
- **The PBR symbol comes out as LaTeX** (`$^{\textcircled{1}}$`). It could be mapped
  back to the symbol in post-processing.
- **Speed is decode-bound, not prefill-bound,** on this page. Column crops are about
  1.5k image tokens and table regions about 3.5k. Regions decode at roughly
  13 tok/s, so a dense table region costs about 200 s.
- **Untested:** whether a free-tier HF CPU without AMX makes bf16 (the Dockerfile
  default) slow there. The vLLM/MTP path needs a GPU and was not tried.
